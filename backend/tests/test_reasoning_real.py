import anthropic
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_passive_coach import started

from chess_tutor.coach.llm import AnthropicLanguageModel
from chess_tutor.coach.reasoning import ReasoningPattern, tag_reasoning
from chess_tutor.models import ConceptEvent, ConceptSource
from chess_tutor.settings import Settings

pytestmark = pytest.mark.llm

# Each answer to "What was your thought?" about 4.Nxe5??, with a pattern a sensible tagger finds.
ANSWERS = [
    ("I saw a free pawn on e5 and just took it.", ReasoningPattern.MATERIAL_GREED),
    (
        "I figured he wouldn't see that Nxe5 recaptures and hope he'd miss it.",
        ReasoningPattern.WISHFUL_THINKING,
    ),
    (
        "I wanted to attack f7 quickly; I didn't think about my king at all.",
        ReasoningPattern.ATTACK_WITHOUT_KING_SAFETY,
    ),
    ("No idea really, it just looked fine.", ReasoningPattern.NO_CLEAR_PLAN),
    (
        "I wanted to win the e5 pawn and didn't look at what Black threatened.",
        ReasoningPattern.IGNORED_OPPONENT_THREAT,
    ),
]


async def test_a_real_model_tags_sample_answers_within_the_taxonomy(settings: Settings) -> None:
    real = settings.model_copy(update={"llm_provider": "anthropic"})
    try:
        api_key = real.llm_api_key.get_secret_value() if real.llm_api_key else None
        await anthropic.AsyncAnthropic(api_key=api_key).models.retrieve(real.cheap_model)
    except (anthropic.AuthenticationError, TypeError):
        pytest.skip("no Anthropic credentials resolve (set ANTHROPIC_API_KEY or `ant auth login`)")

    model = AnthropicLanguageModel(real)
    with started(real, model) as harness:
        harness.play(4)
        engine = harness.app.state.engine
        for text, expected in ANSWERS:
            with Session(engine) as session:
                session.query(ConceptEvent).filter_by(source=ConceptSource.REASONING).delete()
                session.commit()
            await tag_reasoning(engine, model, harness.game_id, 7, text)
            with Session(engine) as session:
                found = list(
                    session.scalars(
                        select(ConceptEvent.concept).where(
                            ConceptEvent.source == ConceptSource.REASONING
                        )
                    )
                )
            print(f"\n{text!r} -> {found}")
            assert set(found) <= {pattern.value for pattern in ReasoningPattern}
            if expected is not None:
                assert expected.value in found
