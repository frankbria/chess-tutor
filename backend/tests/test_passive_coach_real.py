import json

import anthropic
import pytest
from test_passive_coach import started

from chess_tutor.coach.llm import AnthropicLanguageModel
from chess_tutor.settings import Settings

pytestmark = pytest.mark.llm


async def test_a_real_coach_comments_on_a_blunder(settings: Settings) -> None:
    real = settings.model_copy(update={"llm_provider": "anthropic"})
    try:
        api_key = real.llm_api_key.get_secret_value() if real.llm_api_key else None
        await anthropic.AsyncAnthropic(api_key=api_key).models.retrieve(real.cheap_model)
    except (anthropic.AuthenticationError, TypeError):
        pytest.skip("no Anthropic credentials resolve (set ANTHROPIC_API_KEY or `ant auth login`)")

    with started(real, AnthropicLanguageModel(real)) as harness:
        harness.play(4)  # 4.Nxe5?? loses a knight for a pawn

        [stored] = harness.comments()
        print(f"\nCOACH ({stored.source.value}, facts {stored.fact_ids}): {stored.text}")
        assert stored.ply == 7 and stored.text
        fact_ids = json.loads(stored.fact_ids)
        assert isinstance(fact_ids, list) and fact_ids
