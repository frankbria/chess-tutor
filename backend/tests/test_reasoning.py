import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_passive_coach import PRICED, DownLanguageModel, Harness, scripted, started
from test_thought_questions import REPLY, answer, asking

from chess_tutor.coach.llm import LanguageModelError, ModelTier
from chess_tutor.coach.reasoning import ReasoningPattern, ReasoningTags
from chess_tutor.coach.summary import SummaryDraft
from chess_tutor.models import ConceptEvent, ConceptSource, Game, GameSummary
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
        request = model.calls[-1].messages[0].content
        assert "I just wanted the free pawn." in request and "Nxe5" in request
        assert model.calls[-1].tier is ModelTier.CHEAP
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
