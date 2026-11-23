from pathlib import Path

import pytest
from pydantic import BaseModel, SecretStr
from scripted_language_model import ScriptedLanguageModel
from sqlalchemy.orm import Session

from chess_tutor.coach.llm import (
    AnthropicLanguageModel,
    ChatMessage,
    LanguageModel,
    LanguageModelError,
    ModelTier,
    Usage,
    _raise_unless_complete,
    create_language_model,
    record_llm_cost,
)
from chess_tutor.database import create_database_engine, run_migrations, seed_default_player
from chess_tutor.models import Game, GameMode, GameStatus, PlayerColor
from chess_tutor.settings import Settings


class Greeting(BaseModel):
    text: str


def test_coaching_cost_prices_each_token_kind() -> None:
    usage = Usage(
        tier=ModelTier.COACHING,
        input_tokens=1_000_000,
        cache_write_tokens=1_000_000,
        cache_read_tokens=1_000_000,
        output_tokens=1_000_000,
    )

    assert usage.cost == pytest.approx(2.0 + 2.5 + 0.10 + 10.0)


def test_cheap_cost_is_far_below_coaching_for_the_same_tokens() -> None:
    tokens = {"input_tokens": 1000, "cache_read_tokens": 2000, "output_tokens": 500}

    cheap = Usage(tier=ModelTier.CHEAP, **tokens).cost
    coaching = Usage(tier=ModelTier.COACHING, **tokens).cost

    assert cheap == pytest.approx(0.0001 + 0.00002 + 0.00025)
    assert cheap < coaching / 10


async def test_scripted_model_satisfies_the_protocol_and_replays_in_order() -> None:
    model: LanguageModel = ScriptedLanguageModel()
    scripted = ScriptedLanguageModel()
    scripted.queue_structured(Greeting(text="hi"))
    scripted.queue_text("Hel", "lo")
    recorded: list[Usage] = []

    reply = await scripted.structured(
        tier=ModelTier.CHEAP,
        system="s",
        messages=[ChatMessage("user", "x")],
        schema=Greeting,
    )
    deltas = [
        delta
        async for delta in scripted.stream_text(
            tier=ModelTier.COACHING, system="s", messages=[], on_usage=recorded.append
        )
    ]

    assert model is not None
    assert reply.parsed == Greeting(text="hi")
    assert deltas == ["Hel", "lo"]
    assert recorded == [Usage(tier=ModelTier.COACHING)]
    assert [call.tier for call in scripted.calls] == [ModelTier.CHEAP, ModelTier.COACHING]


async def test_scripted_model_rejects_a_reply_of_the_wrong_schema() -> None:
    class Other(BaseModel):
        value: int

    scripted = ScriptedLanguageModel()
    scripted.queue_structured(Other(value=1))

    with pytest.raises(AssertionError):
        await scripted.structured(tier=ModelTier.CHEAP, system="", messages=[], schema=Greeting)


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_incomplete_stop_reasons_raise_a_typed_error(stop_reason: str) -> None:
    with pytest.raises(LanguageModelError):
        _raise_unless_complete(stop_reason)


@pytest.mark.parametrize("stop_reason", ["end_turn", None])
def test_complete_stop_reasons_pass(stop_reason: str | None) -> None:
    _raise_unless_complete(stop_reason)


def test_provider_none_creates_no_model_and_anthropic_needs_no_key(settings: Settings) -> None:
    assert create_language_model(settings) is None

    with_credentials_chain = settings.model_copy(update={"llm_provider": "anthropic"})
    with_key = with_credentials_chain.model_copy(update={"llm_api_key": SecretStr("k")})
    assert isinstance(create_language_model(with_credentials_chain), AnthropicLanguageModel)
    assert isinstance(create_language_model(with_key), AnthropicLanguageModel)


def test_record_llm_cost_accumulates_on_the_game(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, llm_provider="none", database_path=tmp_path / "t.db")
    run_migrations(settings)
    engine = create_database_engine(settings)
    seed_default_player(engine)
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
        assert game.llm_cost == 0

        record_llm_cost(session, game.id, 0.25)
        record_llm_cost(session, game.id, 0.5)
        session.refresh(game)

        assert game.llm_cost == pytest.approx(0.75)
