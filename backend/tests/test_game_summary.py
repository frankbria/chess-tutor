import asyncio
import json
import logging
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError
from scripted_opponent import ScriptedOpponent
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session
from test_passive_coach import (
    COMMENT,
    OPPONENT_REPLIES,
    PLAYER_MOVES,
    PRICED,
    DownLanguageModel,
    Harness,
    scripted,
    started,
)

from chess_tutor.coach.llm import (
    ChatMessage,
    LanguageModel,
    LanguageModelError,
    ModelTier,
    StructuredReply,
)
from chess_tutor.coach.profile import build_coach_profile
from chess_tutor.coach.summary import SUMMARY_SYSTEM_PROMPT, SummaryDraft
from chess_tutor.concepts.taxonomy import Concept
from chess_tutor.main import create_app
from chess_tutor.models import Game, GameMode, GameResult, GameStatus, GameSummary, PlayerColor
from chess_tutor.settings import Settings

DRAFT = SummaryDraft(
    summary="An open game that you lost after giving a piece away in the opening.",
    key_moments=["Move 4 gave a piece away."],
)


def end_game(harness: Harness) -> None:
    """Resign, then wait for the background summary."""
    harness.api.post(f"/games/{harness.game_id}/resign")
    assert harness.api.portal is not None
    harness.api.portal.call(harness.app.state.passive_coach.wait_until_idle)


def stored_summaries(engine: Engine) -> list[GameSummary]:
    with Session(engine) as session:
        return list(session.scalars(select(GameSummary).order_by(GameSummary.id)))


def test_a_finished_game_without_a_model_gets_a_summary_from_its_data(settings: Settings) -> None:
    app = create_app(settings, ScriptedOpponent(*OPPONENT_REPLIES), None)
    with TestClient(app) as api:
        game_id = api.post(
            "/games", json={"mode": "passive", "color": "white", "opponent_elo": 1200}
        ).json()["id"]
        harness = Harness(app, api, game_id)
        harness.play(4)
        end_game(harness)

        [stored] = stored_summaries(app.state.engine)
        with Session(app.state.engine) as session:
            opening = session.get_one(Game, game_id).opening_name
    assert opening and f"in {opening}" in stored.summary
    assert "you lost" in stored.summary and "Mistake patterns tagged" in stored.summary
    assert (stored.game_id, stored.source.value) == (game_id, "template")
    assert json.loads(stored.concepts) and "Your move 4 was rated a blunder." in stored.key_moments


def test_the_summary_prompt_addresses_the_player_as_you() -> None:
    assert 'address the player directly as "you"' in SUMMARY_SYSTEM_PROMPT
    assert "The student" not in SUMMARY_SYSTEM_PROMPT


def test_the_cheap_model_writes_the_summary_and_its_cost_is_recorded(settings: Settings) -> None:
    model = scripted(COMMENT)
    model.queue_structured(DRAFT)
    with started(settings, model) as harness:
        harness.play(4)
        with Session(harness.app.state.engine) as session:
            cost_before = session.get_one(Game, harness.game_id).llm_cost
        end_game(harness)

        [stored] = stored_summaries(harness.app.state.engine)
        assert (stored.summary, json.loads(stored.key_moments)) == (
            DRAFT.summary,
            DRAFT.key_moments,
        )
        assert stored.source.value == "llm" and json.loads(stored.concepts)
        with Session(harness.app.state.engine) as session:
            cost = session.get_one(Game, harness.game_id).llm_cost
    assert cost == pytest.approx(cost_before + PRICED.cost)
    [*_, summary_call] = model.calls
    assert summary_call.tier is ModelTier.CHEAP
    request = json.loads(summary_call.messages[0].content)
    assert request["result_for_student"] == "lost" and request["moments"]


def test_a_game_that_ends_without_pending_coaching_is_summarised_too(settings: Settings) -> None:
    with started(settings, scripted(DRAFT), mode="regular") as harness:
        harness.api.post(f"/games/{harness.game_id}/moves", json={"uci": PLAYER_MOVES[0]})
        end_game(harness)

        [stored] = stored_summaries(harness.app.state.engine)
    assert stored.concepts == "[]" and "you lost" in stored.summary


def test_a_game_resigned_while_its_moves_are_coached_is_summarised_with_their_concepts(
    settings: Settings,
) -> None:
    with started(settings, scripted(COMMENT, DRAFT)) as harness:
        game = f"/games/{harness.game_id}"
        for uci in PLAYER_MOVES[:4]:
            harness.api.post(f"{game}/moves", json={"uci": uci})
        end_game(harness)  # the last move is still being coached when the game ends

        [stored] = stored_summaries(harness.app.state.engine)
    assert stored.summary == DRAFT.summary and json.loads(stored.concepts)


def test_a_failing_model_falls_back_to_the_data_summary(settings: Settings) -> None:
    with started(settings, DownLanguageModel(LanguageModelError("down", PRICED))) as harness:
        harness.play(4)
        end_game(harness)

        [stored] = stored_summaries(harness.app.state.engine)
        assert stored.source.value == "template" and "you lost" in stored.summary
        assert harness.api.get(f"/games/{harness.game_id}").json()["status"] == "finished"


def test_an_unexpected_summary_error_is_logged_and_never_breaks_the_game(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    model: LanguageModel = DownLanguageModel(RuntimeError("boom"))
    with caplog.at_level(logging.ERROR), started(settings, model) as harness:
        harness.play(1)
        end_game(harness)

        assert stored_summaries(harness.app.state.engine) == []
        assert harness.api.get(f"/games/{harness.game_id}").json()["status"] == "finished"
    assert "summarising game" in caplog.text


def test_a_game_is_summarised_only_once(settings: Settings) -> None:
    model = scripted(DRAFT, DRAFT)
    with started(settings, model, mode="regular") as harness:
        end_game(harness)
        assert harness.api.portal is not None
        harness.api.portal.call(harness.app.state.passive_coach._summarize, harness.game_id)

        assert len(stored_summaries(harness.app.state.engine)) == 1
    assert len(model.calls) == 1


def test_the_coach_is_shown_the_latest_past_game_with_the_recurring_concept(
    settings: Settings,
) -> None:
    model = scripted(COMMENT)  # game 1's move comment
    model.queue_structured(DRAFT)  # game 1's summary
    model.queue_structured(COMMENT)  # game 2's move comment
    app = create_app(
        settings, ScriptedOpponent(*OPPONENT_REPLIES[:4], *OPPONENT_REPLIES[:4]), model
    )
    with TestClient(app) as api:
        for finish_game in (True, False):
            game_id = api.post(
                "/games", json={"mode": "passive", "color": "white", "opponent_elo": 1200}
            ).json()["id"]
            harness = Harness(app, api, game_id)
            harness.play(4)
            if finish_game:
                end_game(harness)

    *_, second_comment_call = model.calls
    context = json.loads(second_comment_call.messages[0].content)
    [with_history] = [entry for entry in context["profile"] if "last_game" in entry]
    past = with_history["last_game"]
    today = datetime.now(UTC).strftime("%Y-%m-%d, %A")
    assert (past["date"], past["summary"], past["key_moments"]) == (
        today,
        DRAFT.summary,
        DRAFT.key_moments,
    )


class YieldingSummaryModel:
    """Summarises after yielding to the event loop, so concurrent calls interleave."""

    def __init__(self) -> None:
        self.calls = 0

    async def structured[ParsedModel: BaseModel](
        self,
        *,
        tier: ModelTier,
        system: str,
        messages: Sequence[ChatMessage],
        schema: type[ParsedModel],
    ) -> StructuredReply[ParsedModel]:
        self.calls += 1
        await asyncio.sleep(0)
        if tier is ModelTier.COACHING:
            raise RuntimeError("boom")
        assert isinstance(DRAFT, schema)
        return StructuredReply(DRAFT, PRICED)

    def stream_text(self, **_: object) -> AsyncIterator[str]:
        raise NotImplementedError


def test_an_unfinished_game_is_never_summarised(settings: Settings) -> None:
    with started(settings, scripted(DRAFT), mode="regular") as harness:
        assert harness.api.portal is not None
        harness.api.portal.call(harness.app.state.passive_coach._summarize, harness.game_id)

        assert stored_summaries(harness.app.state.engine) == []
        end_game(harness)
        assert len(stored_summaries(harness.app.state.engine)) == 1


def test_a_failing_coach_call_on_the_final_move_still_leaves_a_summary(settings: Settings) -> None:
    model = YieldingSummaryModel()
    with started(settings, model) as harness:
        harness.play(4)  # the blunder's comment raises inside coach_move
        end_game(harness)

        [stored] = stored_summaries(harness.app.state.engine)
    assert stored.source.value == "llm"


def test_concurrent_summaries_store_one_and_record_every_cost(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    model = YieldingSummaryModel()
    with caplog.at_level(logging.ERROR), started(settings, model, mode="regular") as harness:
        harness.api.post(f"/games/{harness.game_id}/resign")
        coach = harness.app.state.passive_coach

        async def twice() -> None:
            await asyncio.gather(
                coach._summarize(harness.game_id), coach._summarize(harness.game_id)
            )

        assert harness.api.portal is not None
        harness.api.portal.call(coach.wait_until_idle)
        harness.api.portal.call(twice)  # the auto-scheduled one has finished: this pair races on
        with Session(harness.app.state.engine) as session:
            session.query(GameSummary).delete()
            session.commit()
        harness.api.portal.call(twice)

        assert len(stored_summaries(harness.app.state.engine)) == 1
        with Session(harness.app.state.engine) as session:
            cost = session.get_one(Game, harness.game_id).llm_cost
    assert cost >= 2 * PRICED.cost and caplog.text == ""


def test_a_runaway_summary_reply_is_rejected_by_its_schema() -> None:
    with pytest.raises(ValidationError):
        SummaryDraft(summary="x" * 601, key_moments=[])
    with pytest.raises(ValidationError):
        SummaryDraft(summary="ok", key_moments=["a"] * 6)
    with pytest.raises(ValidationError):
        SummaryDraft(summary="ok", key_moments=["x" * 201])


def finished_game_with_summary(
    session: Session,
    ended_at: datetime,
    concepts: list[Concept],
    text: str,
    concepts_json: str | None = None,
) -> None:
    game = Game(
        player_id=1,
        mode=GameMode.PASSIVE,
        player_color=PlayerColor.WHITE,
        opponent_engine="maia3",
        opponent_elo=1200,
        status=GameStatus.FINISHED,
        result=GameResult.BLACK_WINS,
        ended_at=ended_at,
    )
    session.add(game)
    session.flush()
    session.add(
        GameSummary(
            game_id=game.id,
            player_id=1,
            summary=text,
            key_moments="[]",
            concepts=concepts_json or json.dumps([concept.value for concept in concepts]),
            source="template",
        )
    )


def test_a_concepts_entry_carries_its_newest_matching_summary_only(settings: Settings) -> None:
    app = create_app(settings, ScriptedOpponent(), None)
    with TestClient(app), Session(app.state.engine) as session:
        older, newer = datetime(2026, 11, 10, tzinfo=UTC), datetime(2026, 11, 17, tzinfo=UTC)
        finished_game_with_summary(session, older, [Concept.HANGING_PIECE], "old")
        finished_game_with_summary(session, newer, [Concept.HANGING_PIECE], "new")
        finished_game_with_summary(session, datetime(2026, 11, 20, tzinfo=UTC), [], "no concept")
        session.commit()

        profile = build_coach_profile(session, 1, [Concept.HANGING_PIECE, Concept.BACK_RANK], {})

    hanging, back_rank = profile.concepts
    assert hanging.last_game is not None
    assert (hanging.last_game.summary, hanging.last_game.date) == ("new", "2026-11-17, Tuesday")
    assert back_rank.last_game is None


def test_a_corrupt_or_unknown_concept_list_is_skipped(settings: Settings) -> None:
    app = create_app(settings, ScriptedOpponent(), None)
    with TestClient(app), Session(app.state.engine) as session:
        good = datetime(2026, 11, 10, tzinfo=UTC)
        finished_game_with_summary(session, good, [Concept.HANGING_PIECE], "good")
        finished_game_with_summary(session, good, [], "bad json", concepts_json="not json")
        finished_game_with_summary(session, good, [], "unknown", concepts_json='["no_such_thing"]')
        session.commit()

        profile = build_coach_profile(session, 1, [Concept.HANGING_PIECE], {})

    assert profile.concepts[0].last_game is not None
    assert profile.concepts[0].last_game.summary == "good"
