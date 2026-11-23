import logging
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from scripted_language_model import ScriptedLanguageModel
from scripted_opponent import ScriptedOpponent
from sqlalchemy import select
from sqlalchemy.orm import Session

from chess_tutor.api.events import CoachCommentEvent, GameEvent
from chess_tutor.coach.llm import (
    ChatMessage,
    LanguageModel,
    LanguageModelError,
    ModelTier,
    StructuredReply,
    Usage,
)
from chess_tutor.coach.prompt import CoachReply
from chess_tutor.main import create_app
from chess_tutor.models import CoachMessage, ConceptEvent, Game, Move
from chess_tutor.settings import Settings

# 1.e4 e5 2.Nf3 Nc6 3.Bc4 Nf6 4.Nxe5?? Nxe5 5.Bxf7+? Kxf7: the 4th and 5th moves are notable.
OPPONENT_REPLIES = ("e5", "Nc6", "Nf6", "Nxe5", "Kxf7")
PLAYER_MOVES = ("e2e4", "g1f3", "f1c4", "f3e5", "c4f7")
COMMENT = CoachReply(text="That hands your opponent a piece, so look before you grab.", fact_ids=[])
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


def scripted(*replies: CoachReply) -> ScriptedLanguageModel:
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
        assert (stored.text, stored.fact_ids, stored.source.value) == (COMMENT.text, "[]", "llm")
        with Session(harness.app.state.engine) as session:
            game = session.get_one(Game, harness.game_id)
            move = session.scalars(select(Move).where(Move.game_id == game.id, Move.ply == 7)).one()
            concepts = session.scalars(select(ConceptEvent.concept)).all()
            assert game.llm_cost == pytest.approx(PRICED.cost) and PRICED.cost > 0
        assert move.classification == "blunder"
        assert concepts and stored.concepts != "[]"
        [event] = [e for e in harness.events() if isinstance(e, CoachCommentEvent)]
        assert (event.ply, event.text, event.classification) == (7, COMMENT.text, "blunder")
        assert (event.fact_ids, event.source) == ([], "llm")
    [call] = model.calls
    assert (
        call.tier is ModelTier.COACHING and '"event":"comment_on_move"' in call.messages[0].content
    )
    assert '"locked_in":true' in call.messages[0].content


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
        harness.play(4)

        assert model.calls == [] and harness.comments() == []
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
    with started(settings, scripted(COMMENT, COMMENT)) as harness:
        harness.play(5)

        assert [stored.ply for stored in harness.comments()] == [7, 9]


def test_a_game_that_ends_before_its_last_move_is_scored_still_gets_an_accuracy(
    settings: Settings,
) -> None:
    with started(settings, scripted()) as harness:
        game = f"/games/{harness.game_id}"
        harness.api.post(f"{game}/moves", json={"uci": PLAYER_MOVES[0]})
        harness.api.post(f"{game}/resign")  # long before Stockfish has answered
        assert harness.api.get(f"/games/{harness.game_id}").json()["accuracy"] is None
        assert harness.api.portal is not None
        harness.api.portal.call(harness.app.state.passive_coach.wait_until_idle)

        assert harness.api.get(f"/games/{harness.game_id}").json()["accuracy"] is not None
