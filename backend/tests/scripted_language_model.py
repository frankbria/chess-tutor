from collections import deque
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass

from pydantic import BaseModel

from chess_tutor.coach.llm import ChatMessage, ModelTier, StructuredReply, Usage


@dataclass(frozen=True)
class RecordedCall:
    tier: ModelTier
    system: str
    messages: Sequence[ChatMessage]


class ScriptedLanguageModel:
    """Our own deterministic LanguageModel: replays queued replies in order."""

    def __init__(self, usage: Usage | None = None) -> None:
        self.calls: list[RecordedCall] = []
        self._usage = usage
        self._structured_replies: deque[BaseModel] = deque()
        self._text_replies: deque[Sequence[str]] = deque()

    def queue_structured(self, reply: BaseModel) -> None:
        self._structured_replies.append(reply)

    def queue_text(self, *deltas: str) -> None:
        self._text_replies.append(deltas)

    async def structured[ParsedModel: BaseModel](
        self,
        *,
        tier: ModelTier,
        system: str,
        messages: Sequence[ChatMessage],
        schema: type[ParsedModel],
    ) -> StructuredReply[ParsedModel]:
        self.calls.append(RecordedCall(tier, system, messages))
        reply = self._structured_replies.popleft()
        if not isinstance(reply, schema):
            raise AssertionError(f"queued {type(reply).__name__}, asked for {schema.__name__}")
        return StructuredReply(reply, self._usage_for(tier))

    async def stream_text(
        self,
        *,
        tier: ModelTier,
        system: str,
        messages: Sequence[ChatMessage],
        on_usage: Callable[[Usage], None] | None = None,
    ) -> AsyncIterator[str]:
        self.calls.append(RecordedCall(tier, system, messages))
        for delta in self._text_replies.popleft():
            yield delta
        if on_usage is not None:
            on_usage(self._usage_for(tier))

    def _usage_for(self, tier: ModelTier) -> Usage:
        return self._usage or Usage(model=tier.value)
