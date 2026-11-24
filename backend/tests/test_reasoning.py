import asyncio
from collections.abc import AsyncIterator, Sequence

import pytest
from pydantic import BaseModel, ValidationError
from scripted_language_model import ScriptedLanguageModel
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session
from test_passive_coach import (
    GENERIC,
    PLAYER_MOVES,
    PRICED,
    DownLanguageModel,
    Harness,
    scripted,
    started,
)
from test_thought_questions import REPLY, answer, asking

from chess_tutor.coach.llm import ChatMessage, LanguageModelError, ModelTier, StructuredReply
from chess_tutor.coach.reasoning import (
    SYSTEM_PROMPT,
    ReasoningPattern,
    ReasoningTags,
    tag_reasoning,
)
from chess_tutor.coach.summary import SummaryDraft
from chess_tutor.models import ConceptEvent, ConceptSource, Game, GameSummary, Move
from chess_tutor.settings import Settings


def reasoning_events(harness: Harness) -> list[tuple[int, str]]:
    with Session(harness.app.state.engine) as session:
        return [
            (event.ply, event.concept)
            for event in session.scalars(
                select(ConceptEvent)
                .where(ConceptEvent.source == ConceptSource.REASONING)
                .order_by(ConceptEvent.id)
            )
        ]


def test_the_output_schema_rejects_anything_outside_the_taxonomy() -> None:
    with pytest.raises(ValidationError):
        ReasoningTags.model_validate({"patterns": ["bad_vibes"]})
    with pytest.raises(ValidationError):
        ReasoningTags.model_validate({"patterns": ["material_greed"] * 4})
    with pytest.raises(ValidationError):
        ReasoningTags.model_validate({"patterns": [], "confidence": 0.9})
    assert ReasoningTags.model_validate({"patterns": []}).patterns == []
    allowed = ReasoningTags.model_json_schema()["$defs"]["ReasoningPattern"]["enum"]
    assert allowed == [pattern.value for pattern in ReasoningPattern]


def test_an_answer_is_tagged_with_its_patterns_after_the_reply(settings: Settings) -> None:
    tags = ReasoningTags(
        patterns=[
            ReasoningPattern.MATERIAL_GREED,
            ReasoningPattern.IGNORED_OPPONENT_THREAT,
            ReasoningPattern.MATERIAL_GREED,
        ]
    )
    model = scripted(REPLY, tags)
    with started(asking(settings), model) as harness:
        harness.play(4)
        assert answer(harness, "I just wanted the free pawn.") == 202

        assert reasoning_events(harness) == [(7, "material_greed"), (7, "ignored_opponent_threat")]
        [tagging] = [call for call in model.calls if call.system == SYSTEM_PROMPT]
        request = tagging.messages[0].content
        assert "I just wanted the free pawn." in request and "Nxe5" in request
        assert tagging.tier is ModelTier.CHEAP
        with Session(harness.app.state.engine) as session:
            assert session.get_one(Game, harness.game_id).llm_cost == pytest.approx(2 * PRICED.cost)


def test_an_answer_that_shows_no_pattern_stores_nothing(settings: Settings) -> None:
    with started(asking(settings), scripted(REPLY, ReasoningTags(patterns=[]))) as harness:
        harness.play(4)
        assert answer(harness) == 202

        assert reasoning_events(harness) == []


def test_a_failing_model_stores_nothing_and_does_not_block_the_reply(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    model = DownLanguageModel(LanguageModelError("down"))
    with started(asking(settings), model) as harness:
        harness.play(4)
        with caplog.at_level("WARNING"):
            assert answer(harness) == 202

        assert reasoning_events(harness) == []
        assert "reasoning tagging failed" in caplog.text


def test_reasoning_patterns_do_not_break_the_game_summary_or_concept_states(
    settings: Settings,
) -> None:
    tags = ReasoningTags(patterns=[ReasoningPattern.NO_CLEAR_PLAN])
    model = scripted(REPLY, tags, SummaryDraft(summary="A game.", key_moments=[]))
    with started(asking(settings), model) as harness:
        harness.play(4)
        answer(harness)
        assert harness.api.post(f"/games/{harness.game_id}/resign").status_code == 200
        assert harness.api.portal is not None
        harness.api.portal.call(harness.app.state.passive_coach.wait_until_idle)

        assert reasoning_events(harness) == [(7, "no_clear_plan")]
        states = [row["concept"] for row in harness.api.get("/player/concepts").json()]
        assert states == ["hanging_piece"]
        with Session(harness.app.state.engine) as session:
            assert "no_clear_plan" not in session.scalars(select(GameSummary)).one().concepts


async def test_without_a_model_nothing_is_stored(settings: Settings) -> None:
    with started(asking(settings), scripted()) as harness:
        harness.play(4)

        await tag_reasoning(harness.app.state.engine, None, harness.game_id, 7, "a pawn")

        assert reasoning_events(harness) == []


class ReplacesTheMoveWhileThinking:
    """A model whose call lands after a takeback and a different move that reused the row."""

    def __init__(self, engine: Engine, tags: ReasoningTags) -> None:
        self._engine, self._tags = engine, tags

    async def structured[ParsedModel: BaseModel](
        self,
        *,
        tier: ModelTier,
        system: str,
        messages: Sequence[ChatMessage],
        schema: type[ParsedModel],
    ) -> StructuredReply[ParsedModel]:
        with Session(self._engine) as session:
            move = session.scalars(select(Move).where(Move.ply == 7)).one()
            move.uci = "f3g5"  # same row and ply, a different move
            session.commit()
        assert isinstance(self._tags, schema)
        return StructuredReply(self._tags, PRICED)

    def stream_text(self, **_: object) -> AsyncIterator[str]:
        raise NotImplementedError


async def test_tags_for_a_taken_back_move_do_not_attach_to_its_replacement(
    settings: Settings,
) -> None:
    with started(asking(settings), scripted()) as harness:
        harness.play(4)
        engine = harness.app.state.engine
        tags = ReasoningTags(patterns=[ReasoningPattern.MATERIAL_GREED])

        await tag_reasoning(engine, ReplacesTheMoveWhileThinking(engine, tags), 1, 7, "a pawn")

        assert reasoning_events(harness) == []


class BlocksOnReasoning(ScriptedLanguageModel):
    """Replies to the coach from its script, but never answers the reasoning classifier."""

    async def structured[ParsedModel: BaseModel](
        self,
        *,
        tier: ModelTier,
        system: str,
        messages: Sequence[ChatMessage],
        schema: type[ParsedModel],
    ) -> StructuredReply[ParsedModel]:
        if schema is ReasoningTags:
            await asyncio.Event().wait()
        return await super().structured(tier=tier, system=system, messages=messages, schema=schema)


def test_a_stalled_tagging_call_does_not_hold_up_the_next_comment(settings: Settings) -> None:
    model = BlocksOnReasoning(PRICED)
    model.queue_structured(REPLY)
    model.queue_structured(GENERIC)
    with started(asking(settings), model) as harness:
        harness.play(4)
        text = {"text": "a pawn"}
        assert (
            harness.api.post(f"/games/{harness.game_id}/coach/messages", json=text).status_code
            == 202
        )
        move = {"uci": PLAYER_MOVES[4]}
        assert harness.api.post(f"/games/{harness.game_id}/moves", json=move).status_code == 200
        assert harness.api.portal is not None
        harness.api.portal.call(harness.app.state.passive_coach.wait_for_game, harness.game_id)

        assert [(m.ply, m.kind.value) for m in harness.comments()][-1] == (9, "comment")
