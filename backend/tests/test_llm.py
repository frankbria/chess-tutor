from pathlib import Path

import pytest
from anthropic.types import Message, StopReason, TextBlock
from anthropic.types import Usage as SdkUsage
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
    _transport_error,
    create_language_model,
    raise_unless_complete,
    record_llm_cost,
    usage_from,
)
from chess_tutor.database import create_database_engine, run_migrations, seed_default_player
from chess_tutor.models import Game, GameMode, GameStatus, PlayerColor
from chess_tutor.settings import Settings

COACHING = "claude-sonnet-5-5"
CHEAP = "claude-haiku-5-5"


class Greeting(BaseModel):
    text: str


def test_coaching_cost_prices_each_token_kind() -> None:
    usage = Usage(
        model=COACHING,
        input_tokens=1_000_000,
        cache_write_tokens=1_000_000,
        cache_read_tokens=1_000_000,
        output_tokens=1_000_000,
    )

    assert usage.cost == pytest.approx(2.0 + 2.5 + 0.10 + 10.0)


def test_cheap_cost_is_far_below_coaching_for_the_same_tokens() -> None:
    tokens = {"input_tokens": 1000, "cache_read_tokens": 2000, "output_tokens": 500}

    cheap = Usage(model=CHEAP, **tokens).cost
    coaching = Usage(model=COACHING, **tokens).cost

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
    assert recorded == [Usage(model="coaching")]
    assert [call.tier for call in scripted.calls] == [ModelTier.CHEAP, ModelTier.COACHING]


async def test_scripted_model_rejects_a_reply_of_the_wrong_schema() -> None:
    class Other(BaseModel):
        value: int

    scripted = ScriptedLanguageModel()
    scripted.queue_structured(Other(value=1))

    with pytest.raises(AssertionError):
        await scripted.structured(tier=ModelTier.CHEAP, system="", messages=[], schema=Greeting)


def sdk_message(stop_reason: StopReason | None, **usage_fields: int) -> Message:
    return Message(
        id="msg_1",
        content=[TextBlock(type="text", text="hi")],
        model=COACHING,
        role="assistant",
        type="message",
        stop_reason=stop_reason,
        usage=SdkUsage(**{"input_tokens": 10, "output_tokens": 5, **usage_fields}),
    )


def test_usage_is_extracted_from_a_real_sdk_message() -> None:
    message = sdk_message("end_turn", cache_creation_input_tokens=7, cache_read_input_tokens=3)

    assert usage_from(COACHING, message.usage) == Usage(
        model=COACHING,
        input_tokens=10,
        cache_write_tokens=7,
        cache_read_tokens=3,
        output_tokens=5,
    )


def test_absent_cache_counts_are_zero() -> None:
    usage = usage_from(CHEAP, sdk_message("end_turn").usage)

    assert (usage.cache_write_tokens, usage.cache_read_tokens) == (0, 0)


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_incomplete_stop_reasons_raise_with_the_billed_usage(stop_reason: StopReason) -> None:
    message = sdk_message(stop_reason)
    usage = usage_from(COACHING, message.usage)

    with pytest.raises(LanguageModelError) as raised:
        raise_unless_complete(message.stop_reason, usage)

    assert raised.value.usage == usage
    assert raised.value.usage is not None and raised.value.usage.cost > 0


@pytest.mark.parametrize("stop_reason", ["end_turn", "stop_sequence", None])
def test_complete_stop_reasons_pass(stop_reason: StopReason | None) -> None:
    raise_unless_complete(stop_reason, None)


def test_unknown_model_costs_zero_and_logs_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING"):
        cost = Usage(model="claude-mystery-9", input_tokens=1000).cost

    assert cost == 0
    assert "claude-mystery-9" in caplog.text


def test_prices_follow_the_model_id_not_the_tier() -> None:
    assert Usage(model=CHEAP, output_tokens=1_000_000).cost == pytest.approx(0.50)
    assert Usage(model=COACHING, output_tokens=1_000_000).cost == pytest.approx(10.0)


@pytest.fixture
def no_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))


async def test_missing_credentials_become_a_language_model_error(
    settings: Settings, no_credentials: None
) -> None:
    model = AnthropicLanguageModel(settings)

    with pytest.raises(LanguageModelError):
        await model.structured(tier=ModelTier.CHEAP, system="s", messages=[], schema=Greeting)
    reported: list[Usage] = []
    with pytest.raises(LanguageModelError):
        async for _ in model.stream_text(
            tier=ModelTier.CHEAP, system="s", messages=[], on_usage=reported.append
        ):
            pass
    assert reported == []


def test_other_type_errors_are_not_swallowed() -> None:
    with pytest.raises(TypeError):
        _transport_error(TypeError("a real bug"))


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
