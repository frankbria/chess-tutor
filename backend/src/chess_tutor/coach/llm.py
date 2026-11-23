from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Protocol

import anthropic
from anthropic.types import MessageParam, TextBlockParam
from pydantic import BaseModel
from sqlalchemy import update
from sqlalchemy.orm import Session

from chess_tutor.models import Game
from chess_tutor.settings import Settings

TOKENS_PER_MILLION = 1_000_000
# Coaching replies are short; the headroom is for adaptive thinking, which counts against it.
COACHING_MAX_TOKENS = 4096
CHEAP_MAX_TOKENS = 2048
# Spec 6.6 wants the first token within about 2 s, so both tiers think as little as the API allows.
EFFORT: Literal["low"] = "low"


class ModelTier(StrEnum):
    COACHING = "coaching"
    CHEAP = "cheap"


class LanguageModelError(Exception):
    """The model refused, ran out of output tokens, or could not be reached."""


@dataclass(frozen=True)
class ChatMessage:
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class TierPrices:
    """US dollars per million tokens."""

    input: float
    cache_read: float
    output: float
    cache_write_multiplier: float = 1.25  # 5-minute cache writes cost 1.25x the input rate


# shortcut: Haiku 5.5 doubles+ its rates above 100K-token prompts and prices follow the default
# models; coaching prompts stay far below that, so revisit if tiers are pointed at other models.
PRICES: dict[ModelTier, TierPrices] = {
    ModelTier.COACHING: TierPrices(input=2.0, cache_read=0.10, output=10.0),
    ModelTier.CHEAP: TierPrices(input=0.10, cache_read=0.01, output=0.50),
}


@dataclass(frozen=True)
class Usage:
    tier: ModelTier
    input_tokens: int = 0  # uncached input only
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0

    @property
    def cost(self) -> float:
        prices = PRICES[self.tier]
        return (
            self.input_tokens * prices.input
            + self.cache_write_tokens * prices.input * prices.cache_write_multiplier
            + self.cache_read_tokens * prices.cache_read
            + self.output_tokens * prices.output
        ) / TOKENS_PER_MILLION


@dataclass(frozen=True)
class StructuredReply[ParsedModel: BaseModel]:
    parsed: ParsedModel
    usage: Usage


class LanguageModel(Protocol):
    async def structured[ParsedModel: BaseModel](
        self,
        *,
        tier: ModelTier,
        system: str,
        messages: Sequence[ChatMessage],
        schema: type[ParsedModel],
    ) -> StructuredReply[ParsedModel]: ...

    def stream_text(
        self,
        *,
        tier: ModelTier,
        system: str,
        messages: Sequence[ChatMessage],
        on_usage: Callable[[Usage], None] | None = None,
    ) -> AsyncIterator[str]: ...


class AnthropicLanguageModel:
    # No server-side `fallbacks`: the API ships them as a beta, and a refusal is better surfaced
    # as a LanguageModelError so the coach can fall back to its deterministic template (spec 6.4).
    def __init__(self, settings: Settings) -> None:
        self._models = {
            ModelTier.COACHING: settings.coaching_model,
            ModelTier.CHEAP: settings.cheap_model,
        }
        api_key = settings.llm_api_key.get_secret_value().strip() if settings.llm_api_key else ""
        # With no key the SDK resolves credentials itself (ANTHROPIC_API_KEY or `ant auth login`).
        self._client = (
            anthropic.AsyncAnthropic(api_key=api_key) if api_key else (anthropic.AsyncAnthropic())
        )

    async def structured[ParsedModel: BaseModel](
        self,
        *,
        tier: ModelTier,
        system: str,
        messages: Sequence[ChatMessage],
        schema: type[ParsedModel],
    ) -> StructuredReply[ParsedModel]:
        try:
            response = await self._client.messages.parse(
                model=self._models[tier],
                max_tokens=_max_tokens(tier),
                system=_cached_system(system),
                messages=_to_params(messages),
                output_format=schema,
                output_config={"effort": EFFORT},
            )
        except anthropic.APIError as error:
            raise LanguageModelError(str(error)) from error
        _raise_unless_complete(response.stop_reason)
        if response.parsed_output is None:
            raise LanguageModelError("the model returned no structured output")
        return StructuredReply(response.parsed_output, _usage(tier, response.usage))

    async def stream_text(
        self,
        *,
        tier: ModelTier,
        system: str,
        messages: Sequence[ChatMessage],
        on_usage: Callable[[Usage], None] | None = None,
    ) -> AsyncIterator[str]:
        try:
            async with self._client.messages.stream(
                model=self._models[tier],
                max_tokens=_max_tokens(tier),
                system=_cached_system(system),
                messages=_to_params(messages),
                output_config={"effort": EFFORT},
            ) as stream:
                async for delta in stream.text_stream:
                    yield delta
                final = await stream.get_final_message()
        except anthropic.APIError as error:
            raise LanguageModelError(str(error)) from error
        _raise_unless_complete(final.stop_reason)
        if on_usage is not None:
            on_usage(_usage(tier, final.usage))


def create_language_model(settings: Settings) -> LanguageModel | None:
    """The configured provider, or None when the coach is switched off."""
    if settings.llm_provider == "anthropic":
        return AnthropicLanguageModel(settings)
    return None


def record_llm_cost(session: Session, game_id: int, cost: float) -> None:
    # An in-SQL increment so concurrent calls for one game cannot overwrite each other.
    session.execute(update(Game).where(Game.id == game_id).values(llm_cost=Game.llm_cost + cost))
    session.commit()


def _max_tokens(tier: ModelTier) -> int:
    return COACHING_MAX_TOKENS if tier is ModelTier.COACHING else CHEAP_MAX_TOKENS


def _cached_system(system: str) -> list[TextBlockParam]:
    # The breakpoint after the static prompt caches it (and any tools) across calls.
    return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]


def _to_params(messages: Sequence[ChatMessage]) -> list[MessageParam]:
    return [
        (
            {"role": "user", "content": message.content}
            if message.role == "user"
            else {"role": "assistant", "content": message.content}
        )
        for message in messages
    ]


def _raise_unless_complete(stop_reason: str | None) -> None:
    if stop_reason == "refusal":
        raise LanguageModelError("the model refused the request")
    if stop_reason == "max_tokens":
        raise LanguageModelError("the reply was cut off at the output token limit")


def _usage(tier: ModelTier, usage: anthropic.types.Usage) -> Usage:
    return Usage(
        tier=tier,
        input_tokens=usage.input_tokens,
        cache_write_tokens=usage.cache_creation_input_tokens or 0,
        cache_read_tokens=usage.cache_read_input_tokens or 0,
        output_tokens=usage.output_tokens,
    )
