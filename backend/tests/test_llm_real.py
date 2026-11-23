from pathlib import Path

import anthropic
import pytest
from pydantic import BaseModel
from sqlalchemy.orm import Session

from chess_tutor.coach.llm import (
    AnthropicLanguageModel,
    ChatMessage,
    ModelTier,
    Usage,
    record_llm_cost,
)
from chess_tutor.database import create_database_engine, run_migrations, seed_default_player
from chess_tutor.models import Game, GameMode, GameStatus, PlayerColor
from chess_tutor.settings import Settings

pytestmark = pytest.mark.llm


class Answer(BaseModel):
    capital: str


@pytest.fixture
async def settings(tmp_path: Path) -> Settings:
    settings = Settings(_env_file=None, llm_provider="anthropic", database_path=tmp_path / "t.db")
    try:
        api_key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else None
        await anthropic.AsyncAnthropic(api_key=api_key).models.retrieve(settings.cheap_model)
    except (anthropic.AuthenticationError, TypeError):
        pytest.skip("no Anthropic credentials resolve (set ANTHROPIC_API_KEY or `ant auth login`)")
    return settings


async def test_structured_reply_parses_on_the_cheap_tier(settings: Settings) -> None:
    reply = await AnthropicLanguageModel(settings).structured(
        tier=ModelTier.CHEAP,
        system="Answer with the capital city only.",
        messages=[ChatMessage("user", "France")],
        schema=Answer,
    )

    assert reply.parsed.capital == "Paris"
    assert reply.usage.output_tokens > 0


async def test_coaching_tier_streams_text_and_reports_usage(settings: Settings) -> None:
    usages: list[Usage] = []

    deltas = [
        delta
        async for delta in AnthropicLanguageModel(settings).stream_text(
            tier=ModelTier.COACHING,
            system="Reply in one short sentence.",
            messages=[ChatMessage("user", "Say hello.")],
            on_usage=usages.append,
        )
    ]

    assert "".join(deltas).strip()
    assert len(usages) == 1
    assert usages[0].cost > 0


async def test_usage_cost_is_recorded_on_a_game(settings: Settings) -> None:
    run_migrations(settings)
    engine = create_database_engine(settings)
    seed_default_player(engine)
    reply = await AnthropicLanguageModel(settings).structured(
        tier=ModelTier.CHEAP,
        system="Answer with the capital city only.",
        messages=[ChatMessage("user", "Japan")],
        schema=Answer,
    )
    with Session(engine) as session:
        game = Game(
            player_id=1,
            mode=GameMode.PASSIVE,
            player_color=PlayerColor.WHITE,
            opponent_engine="maia3",
            opponent_elo=1200,
            status=GameStatus.ACTIVE,
        )
        session.add(game)
        session.commit()

        record_llm_cost(session, game.id, reply.usage.cost)
        session.refresh(game)

        assert reply.usage.cost > 0
        assert game.llm_cost == pytest.approx(reply.usage.cost)


async def test_stopping_a_stream_early_still_reports_usage_once(settings: Settings) -> None:
    usages: list[Usage] = []
    stream = AnthropicLanguageModel(settings).stream_text(
        tier=ModelTier.COACHING,
        system="Write a long story.",
        messages=[ChatMessage("user", "Begin.")],
        on_usage=usages.append,
    )

    async for _ in stream:
        break
    await stream.aclose()

    assert len(usages) == 1
    assert usages[0].input_tokens + usages[0].cache_write_tokens + usages[0].cache_read_tokens > 0
