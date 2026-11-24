import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from scripted_language_model import ScriptedLanguageModel
from scripted_opponent import ScriptedOpponent
from sqlalchemy import select
from sqlalchemy.orm import Session

from chess_tutor.api.events import CoachCommentEvent, GameEvent
from chess_tutor.api.settings import CoachVerbosity
from chess_tutor.coach import passive
from chess_tutor.coach.llm import (
    ChatMessage,
    LanguageModel,
    LanguageModelError,
    ModelTier,
    StructuredReply,
    Usage,
)
from chess_tutor.coach.prompt import VERBOSITY_DIRECTIVES, CoachReply
from chess_tutor.concepts.state import new_state
from chess_tutor.concepts.taxonomy import Concept
from chess_tutor.main import create_app
from chess_tutor.models import (
    CoachMessage,
    CoachMessageKind,
    CoachMessageSource,
    CoachRole,
    ConceptEvent,
    ConceptState,
    ConceptStatus,
    Game,
    GameMode,
    GameStatus,
    Move,
    PlayerColor,
)
from chess_tutor.settings import Settings

# 1.e4 e5 2.Nf3 Nc6 3.Bc4 Nf6 4.Nxe5?? Nxe5 5.Bxf7+? Kxf7: the 4th and 5th moves are notable.
OPPONENT_REPLIES = ("e5", "Nc6", "Nf6", "Nxe5", "Kxf7")
PLAYER_MOVES = ("e2e4", "g1f3", "f1c4", "f3e5", "c4f7")
COMMENT = CoachReply(
    text="Nxe5 wins a pawn, but Black recaptures with Nxe5 and then your bishop on c4 hangs.",
    fact_ids=["allows:c6e5"],
)
GENERIC = CoachReply(
    text="Another piece given away; check what each move leaves hanging.", fact_ids=[]
)
UNGROUNDED = CoachReply(text="Nxe5 loses a piece to Qd8.", fact_ids=["made_up:id"])
PRICED = Usage(model="claude-sonnet-5-5", input_tokens=1000, output_tokens=100)


class DownLanguageModel:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def structured[ParsedModel: BaseModel](
        self,
        *,
        tier: ModelTier,
        system: str,
        messages: Sequence[ChatMessage],
        schema: type[ParsedModel],
    ) -> StructuredReply[ParsedModel]:
        raise self._error

    async def stream_text(
        self,
        *,
        tier: ModelTier,
        system: str,
        messages: Sequence[ChatMessage],
        on_usage: Callable[[Usage], None] | None = None,
    ) -> AsyncIterator[str]:
        raise self._error
        yield


class HangingLanguageModel(DownLanguageModel):
    async def structured[ParsedModel: BaseModel](
        self,
        *,
        tier: ModelTier,
        system: str,
        messages: Sequence[ChatMessage],
        schema: type[ParsedModel],
    ) -> StructuredReply[ParsedModel]:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class Harness:
    def __init__(self, app: FastAPI, api: TestClient, game_id: int) -> None:
        self.app, self.api, self.game_id = app, api, game_id
        self._subscription = app.state.game_events.subscribe(game_id)
        self.queue = self._subscription.__enter__()
        self.played = 0

    def play(self, count: int) -> None:
        """Play that many more player moves, then wait for the background coach to finish."""
        for uci in PLAYER_MOVES[self.played : self.played + count]:
            response = self.api.post(f"/games/{self.game_id}/moves", json={"uci": uci})
            assert response.status_code == 200
        self.played += count
        assert self.api.portal is not None
        self.api.portal.call(self.app.state.passive_coach.wait_until_idle)

    def events(self) -> list[GameEvent]:
        return [self.queue.get_nowait() for _ in range(self.queue.qsize())]

    def comments(self) -> list[CoachMessage]:
        with Session(self.app.state.engine) as session:
            return list(session.scalars(select(CoachMessage).order_by(CoachMessage.id)))


@contextmanager
def started(settings: Settings, model: LanguageModel, mode: str = "passive") -> Iterator[Harness]:
    app = create_app(settings, ScriptedOpponent(*OPPONENT_REPLIES), model)
    with TestClient(app) as api:
        body = {"mode": mode, "color": "white", "opponent_elo": 1200}
        yield Harness(app, api, api.post("/games", json=body).json()["id"])


def scripted(*replies: BaseModel) -> ScriptedLanguageModel:
    model = ScriptedLanguageModel(PRICED)
    for reply in replies:
        model.queue_structured(reply)
    return model


def test_a_blunder_gets_a_grounded_comment(settings: Settings) -> None:
    model = scripted(COMMENT)
    with started(settings, model) as harness:
        harness.play(4)

        [stored] = harness.comments()
        assert (stored.ply, stored.role.value, stored.kind.value) == (7, "coach", "comment")
        assert (stored.text, stored.source.value) == (COMMENT.text, "llm")
        assert stored.fact_ids == '["allows:c6e5"]'
        with Session(harness.app.state.engine) as session:
            game = session.get_one(Game, harness.game_id)
            move = session.scalars(select(Move).where(Move.game_id == game.id, Move.ply == 7)).one()
            concepts = session.scalars(select(ConceptEvent.concept)).all()
            assert game.llm_cost == pytest.approx(PRICED.cost) and PRICED.cost > 0
        assert move.classification == "blunder"
        assert concepts and stored.concepts != "[]"
        [event] = [e for e in harness.events() if isinstance(e, CoachCommentEvent)]
        assert (event.ply, event.text, event.classification) == (7, COMMENT.text, "blunder")
        assert event.phase == "opening"
        assert (event.fact_ids, event.source) == (["allows:c6e5"], "llm")
    [call] = model.calls
    assert (
        call.tier is ModelTier.COACHING and '"event":"comment_on_move"' in call.messages[0].content
    )
    assert '"locked_in":true' in call.messages[0].content


@pytest.mark.parametrize("stored", ["brief", "detailed"])
def test_comments_use_the_stored_verbosity(settings: Settings, stored: CoachVerbosity) -> None:
    model = scripted(COMMENT)
    with started(settings, model) as harness:
        harness.api.put(
            "/settings",
            json={
                "coach_verbosity": stored,
                "default_mode": "passive",
                "maia_model": "3m",
                "monthly_cost_ceiling": 10,
            },
        )
        harness.play(4)

    [call] = model.calls
    payload = json.loads(call.messages[0].content)
    assert payload["verbosity"] == VERBOSITY_DIRECTIVES[stored]
    assert "Overrides the usual length" not in call.system


def test_default_verbosity_adds_no_length_directive(settings: Settings) -> None:
    model = scripted(COMMENT)
    with started(settings, model) as harness:
        harness.play(4)

    assert "verbosity" not in model.calls[0].messages[0].content


def test_stored_comments_are_served_with_their_classification(settings: Settings) -> None:
    with started(settings, scripted(COMMENT)) as harness:
        assert harness.api.get(f"/games/{harness.game_id}/coach/messages").json() == []
        harness.play(4)

        [message] = harness.api.get(f"/games/{harness.game_id}/coach/messages").json()
        assert message.pop("created_at")
        assert message == {
            "ply": 7,
            "role": "coach",
            "kind": "comment",
            "text": COMMENT.text,
            "classification": "blunder",
            "phase": "opening",
            "hint_level": None,
        }
        assert harness.api.get("/games/999/coach/messages").status_code == 404


def test_ordinary_good_moves_make_no_llm_call(settings: Settings) -> None:
    model = scripted()
    with started(settings, model) as harness:
        harness.play(3)

        assert model.calls == [] and harness.comments() == []
        assert not [e for e in harness.events() if isinstance(e, CoachCommentEvent)]
        with Session(harness.app.state.engine) as session:
            labels = session.scalars(select(Move.classification).where(Move.ply % 2 == 1)).all()
        assert set(labels) <= {"best", "good"} and len(labels) == 3


@pytest.mark.parametrize("mode", ["regular", "active"])
def test_other_modes_get_no_coaching(settings: Settings, mode: str) -> None:
    model = scripted()
    with started(settings, model, mode) as harness:
        if mode == "active":
            for uci in PLAYER_MOVES[:4]:
                body = {"uci": uci, "propose": True}
                assert harness.api.post(f"/games/{harness.game_id}/moves", json=body).is_success
                assert harness.api.post(f"/games/{harness.game_id}/moves/lock").is_success
            assert harness.api.portal is not None
            harness.api.portal.call(harness.app.state.hint_coach.wait_until_idle)
        else:
            harness.play(4)

        # Active mode's own hints (before lock-in) are not passive comments.
        expected_calls = 1 if mode == "active" else 0
        assert len(model.calls) == expected_calls
        assert {message.kind.value for message in harness.comments()} <= {"hint"}
        with Session(harness.app.state.engine) as session:
            assert session.scalars(select(Move.classification).where(Move.ply == 7)).one() is None


def test_a_failing_llm_falls_back_to_a_template(settings: Settings) -> None:
    with started(settings, DownLanguageModel(LanguageModelError("down", PRICED))) as harness:
        harness.play(4)

        [stored] = harness.comments()
        assert stored.source.value == "template" and stored.text
        with Session(harness.app.state.engine) as session:
            assert session.get_one(Game, harness.game_id).llm_cost > 0
        assert harness.api.get(f"/games/{harness.game_id}").json()["status"] == "active"


def test_an_unexpected_coach_error_is_logged_and_never_breaks_the_game(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    with started(settings, DownLanguageModel(RuntimeError("bug"))) as harness:
        with caplog.at_level(logging.ERROR):
            harness.play(4)

        assert "passive coaching failed" in caplog.text
        assert harness.comments() == []
        assert harness.api.get(f"/games/{harness.game_id}").json()["moves"][-1] == "Nxe5"


def test_comments_are_rate_limited(settings: Settings) -> None:
    model = scripted(COMMENT, COMMENT)
    with started(settings.model_copy(update={"coach_comment_every_n_moves": 2}), model) as harness:
        harness.play(5)  # two notable moves in a row

        assert [stored.ply for stored in harness.comments()] == [7]
        with Session(harness.app.state.engine) as session:
            assert session.scalars(select(Move.classification).where(Move.ply == 9)).one() != "good"


def test_every_notable_move_is_commented_by_default(settings: Settings) -> None:
    with started(settings, scripted(COMMENT, GENERIC)) as harness:
        harness.play(5)

        assert [stored.ply for stored in harness.comments()] == [7, 9]


def test_a_game_that_ends_before_its_last_move_is_scored_still_gets_an_accuracy(
    settings: Settings,
) -> None:
    with started(settings, scripted()) as harness:
        game = f"/games/{harness.game_id}"
        harness.api.post(f"{game}/moves", json={"uci": PLAYER_MOVES[0]})
        harness.api.post(f"{game}/resign")  # long before Stockfish has answered
        assert harness.api.get(f"/games/{harness.game_id}").json()["accuracy"] is not None


def test_an_ungrounded_reply_is_rejected_and_regenerated(settings: Settings) -> None:
    model = scripted(UNGROUNDED, COMMENT)
    with started(settings, model) as harness:
        harness.play(4)

        [stored] = harness.comments()
        assert (stored.text, stored.source.value) == (COMMENT.text, "llm_regenerated")
    assert len(model.calls) == 2


def test_a_move_is_coached_even_when_the_opponent_fails_to_reply(settings: Settings) -> None:
    model = scripted(COMMENT)
    app = create_app(settings, ScriptedOpponent("e5", "Nc6", "Nf6"), model)
    with TestClient(app) as api:
        game_id = api.post(
            "/games", json={"mode": "passive", "color": "white", "opponent_elo": 1200}
        ).json()["id"]
        for uci in PLAYER_MOVES[:3]:
            assert api.post(f"/games/{game_id}/moves", json={"uci": uci}).status_code == 200
        assert api.post(f"/games/{game_id}/moves", json={"uci": "f3e5"}).status_code == 503
        assert api.portal is not None
        api.portal.call(app.state.passive_coach.wait_until_idle)

        with Session(app.state.engine) as session:
            assert session.scalars(select(CoachMessage.ply)).all() == [7]


def test_the_final_comment_and_concepts_land_before_the_game_closes(settings: Settings) -> None:
    with started(settings, scripted(COMMENT)) as harness:
        game = f"/games/{harness.game_id}"
        for uci in PLAYER_MOVES[:4]:
            harness.api.post(f"{game}/moves", json={"uci": uci})
        harness.api.post(f"{game}/resign")  # while the last move is still being coached

        assert [event.type for event in harness.events()][-2:] == ["coach_comment", "game_over"]
        with Session(harness.app.state.engine) as session:
            assert session.scalars(select(ConceptState.concept)).all()


def test_a_stalled_llm_cannot_hold_resign_open(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(passive, "FINAL_COMMENT_WAIT_SECONDS", 0.2)
    with started(settings, HangingLanguageModel(LanguageModelError("unused"))) as harness:
        game = f"/games/{harness.game_id}"
        for uci in PLAYER_MOVES[:4]:
            harness.api.post(f"{game}/moves", json={"uci": uci})

        assert harness.api.post(f"{game}/resign").status_code == 200
        assert [event.type for event in harness.events()][-1] == "game_over"


def test_two_pending_moves_then_a_resign_still_refresh_the_concepts(settings: Settings) -> None:
    with started(settings, scripted(COMMENT, GENERIC)) as harness:
        game = f"/games/{harness.game_id}"
        for uci in PLAYER_MOVES:
            harness.api.post(f"{game}/moves", json={"uci": uci})
        harness.api.post(f"{game}/resign")

        with Session(harness.app.state.engine) as session:
            assert session.scalars(select(ConceptState.concept)).all()
            assert session.get_one(Game, harness.game_id).accuracy is not None


def test_a_passive_comment_carries_the_profile_and_the_stored_phrasings(
    settings: Settings,
) -> None:
    model = scripted(COMMENT)
    with started(settings, model) as harness:
        with Session(harness.app.state.engine) as session:
            row = new_state(1, Concept.HANGING_PIECE.value)
            row.state, row.count = ConceptStatus.RECURRING, 3
            row.updated_at = datetime(2026, 11, 23, tzinfo=UTC)
            earlier = Game(
                player_id=1,
                mode=GameMode.PASSIVE,
                player_color=PlayerColor.WHITE,
                opponent_engine="maia3",
                opponent_elo=1200,
                status=GameStatus.FINISHED,
            )
            session.add_all([row, earlier])
            session.flush()
            session.add(
                CoachMessage(
                    game_id=earlier.id,
                    ply=7,
                    role=CoachRole.COACH,
                    kind=CoachMessageKind.COMMENT,
                    text="Yesterday's wording about loose pieces.",
                    fact_ids="[]",
                    source=CoachMessageSource.LLM,
                    concepts='["hanging_piece"]',
                )
            )
            session.commit()

        harness.play(4)

    payload = json.loads(model.calls[0].messages[0].content)
    entry = next(item for item in payload["profile"] if item["concept"] == "hanging_piece")
    assert entry["state"] == "recurring" and "3 games" in entry["style"]
    assert entry["recent_phrasings"] == ["Yesterday's wording about loose pieces."]


class GatedLanguageModel(ScriptedLanguageModel):
    """Holds its reply until released, so a move can be taken back while the coach is mid-call."""

    def __init__(self) -> None:
        super().__init__(PRICED)
        self.queue_structured(COMMENT)
        self._open = asyncio.Event()
        self.waiting = False

    def release(self) -> None:
        self._open.set()

    async def structured[ParsedModel: BaseModel](
        self,
        *,
        tier: ModelTier,
        system: str,
        messages: Sequence[ChatMessage],
        schema: type[ParsedModel],
    ) -> StructuredReply[ParsedModel]:
        self.waiting = True
        await self._open.wait()
        return await super().structured(tier=tier, system=system, messages=messages, schema=schema)


def test_a_comment_in_flight_for_a_taken_back_move_is_never_stored(settings: Settings) -> None:
    model = GatedLanguageModel()
    with started(settings, model) as harness:
        for uci in PLAYER_MOVES[:4]:
            assert harness.api.post(f"/games/{harness.game_id}/moves", json={"uci": uci}).is_success
        deadline = time.monotonic() + 30
        while not model.waiting and time.monotonic() < deadline:
            time.sleep(0.05)
        assert harness.api.post(f"/games/{harness.game_id}/takeback").status_code == 200
        assert harness.api.portal is not None
        harness.api.portal.call(model.release)
        harness.api.portal.call(harness.app.state.passive_coach.wait_until_idle)

        assert harness.comments() == []
        assert not [e for e in harness.events() if isinstance(e, CoachCommentEvent)]
        with Session(harness.app.state.engine) as session:
            assert session.scalars(select(ConceptEvent)).all() == []


def test_taking_a_move_back_erases_its_stored_comment_and_concepts(settings: Settings) -> None:
    with started(settings, scripted(COMMENT)) as harness:
        harness.play(4)
        assert len(harness.comments()) == 1

        harness.api.post(f"/games/{harness.game_id}/takeback")

        assert harness.comments() == []
        with Session(harness.app.state.engine) as session:
            assert session.scalars(select(ConceptEvent)).all() == []
            assert session.scalars(
                select(Move.ply).where(Move.game_id == harness.game_id)
            ).all() == [*range(1, 7)]


def test_a_different_move_reusing_the_row_id_does_not_inherit_the_stale_comment(
    settings: Settings,
) -> None:
    model = GatedLanguageModel()
    with started(settings, model) as harness:
        game = f"/games/{harness.game_id}"
        for uci in PLAYER_MOVES[:4]:
            assert harness.api.post(f"{game}/moves", json={"uci": uci}).is_success
        deadline = time.monotonic() + 30
        while not model.waiting and time.monotonic() < deadline:
            time.sleep(0.05)
        assert harness.api.post(f"{game}/takeback").status_code == 200
        harness.api.post(f"{game}/moves", json={"uci": "d2d3"})  # SQLite reuses the freed row id
        assert harness.api.portal is not None
        harness.api.portal.call(model.release)
        harness.api.portal.call(harness.app.state.passive_coach.wait_until_idle)

        assert harness.comments() == []
