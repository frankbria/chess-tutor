import asyncio
import logging
from collections.abc import Sequence

import pytest
from pydantic import BaseModel
from scripted_language_model import ScriptedLanguageModel
from test_coach_grounding import GOOD, USAGE, context_for

from chess_tutor.coach.latency import LatencyTracker, reply_with_instant_template
from chess_tutor.coach.llm import ChatMessage, ModelTier, StructuredReply
from chess_tutor.coach.prompt import CoachReply


class GatedLanguageModel(ScriptedLanguageModel):
    """Holds every structured reply until the test opens the gate."""

    def __init__(self) -> None:
        super().__init__(USAGE)
        self.gate = asyncio.Event()

    async def structured[ParsedModel: BaseModel](
        self,
        *,
        tier: ModelTier,
        system: str,
        messages: Sequence[ChatMessage],
        schema: type[ParsedModel],
    ) -> StructuredReply[ParsedModel]:
        await self.gate.wait()
        return await super().structured(tier=tier, system=system, messages=messages, schema=schema)


async def test_the_template_is_shown_before_the_gated_model_replies() -> None:
    model = GatedLanguageModel()
    model.queue_structured(GOOD)
    shown: list[CoachReply] = []
    context = context_for()

    task = asyncio.create_task(
        reply_with_instant_template(model, context, 0.0, shown.append, LatencyTracker())
    )
    await asyncio.sleep(0.05)
    assert len(shown) == 1 and not task.done()  # the template is out while the model is blocked
    model.gate.set()
    reply = await task
    assert reply.text == GOOD.text and shown[0].text != GOOD.text


def test_latency_is_logged_with_rolling_percentiles(caplog: pytest.LogCaptureFixture) -> None:
    tracker = LatencyTracker(log_every=3)
    with caplog.at_level(logging.INFO, logger="chess_tutor.coach.latency"):
        for seconds in (1.0, 2.0, 3.0):
            tracker.record(to_template=0.1, to_final=seconds)

    [line] = [r.getMessage() for r in caplog.records if "coach latency" in r.getMessage()]
    assert "template p50=0.100s p95=0.100s" in line
    assert "final p50=2.000s p95=3.000s" in line and "n=3" in line


def test_no_summary_is_logged_before_the_interval(caplog: pytest.LogCaptureFixture) -> None:
    tracker = LatencyTracker(log_every=5)
    with caplog.at_level(logging.INFO, logger="chess_tutor.coach.latency"):
        tracker.record(to_template=0.1, to_final=1.0)
    assert "coach latency" not in caplog.text


async def test_each_reply_records_its_latency_and_the_summary_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    model = ScriptedLanguageModel(USAGE)
    model.queue_structured(GOOD)
    tracker = LatencyTracker(log_every=1)
    with caplog.at_level(logging.INFO, logger="chess_tutor.coach.latency"):
        await reply_with_instant_template(model, context_for(), 0.0, lambda _: None, tracker)

    assert "coach latency template p50=" in caplog.text
