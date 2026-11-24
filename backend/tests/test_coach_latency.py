import asyncio
import logging
from collections.abc import Sequence

import pytest
from pydantic import BaseModel
from scripted_language_model import ScriptedLanguageModel
from test_coach_grounding import GOOD, USAGE, context_for

from chess_tutor.api.events import CoachCommentEvent, GameEvent, GameEvents
from chess_tutor.coach.grounding import GroundedReply
from chess_tutor.coach.latency import LatencyTracker, reply_with_instant_template
from chess_tutor.coach.llm import ChatMessage, ModelTier, StructuredReply
from chess_tutor.coach.prompt import CoachReply
from chess_tutor.openings.phase import GamePhase


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


def template_event(template: CoachReply) -> CoachCommentEvent:
    return CoachCommentEvent(
        ply=1,
        text=template.text,
        classification="blunder",
        phase=GamePhase.OPENING,
        fact_ids=template.fact_ids,
        source="template",
        provisional=True,
        message_key="key",
    )


def drained_queue(queue: asyncio.Queue[GameEvent]) -> list[CoachCommentEvent]:
    found = [queue.get_nowait() for _ in range(queue.qsize())]
    return [event for event in found if isinstance(event, CoachCommentEvent)]


async def test_the_template_is_shown_before_the_gated_model_replies() -> None:
    model = GatedLanguageModel()
    model.queue_structured(GOOD)
    events = GameEvents()
    settled: list[str] = []

    def settle(reply: GroundedReply) -> bool:
        settled.append(reply.text)
        return True

    with events.subscribe(1) as queue:
        task = asyncio.create_task(
            reply_with_instant_template(
                model,
                context_for(),
                0.0,
                events,
                1,
                template_event,
                settle,
                LatencyTracker(),
            )
        )
        await asyncio.sleep(0.05)
        assert queue.qsize() == 1 and not task.done() and settled == []  # the model is blocked
        model.gate.set()
        await task
    assert settled == [GOOD.text] and queue.qsize() == 1  # a settled reply is not retracted


async def test_a_suppressed_final_is_retracted_under_the_same_key() -> None:
    model = ScriptedLanguageModel(USAGE)
    model.queue_structured(GOOD)
    events = GameEvents()
    tracker = LatencyTracker(log_every=1)
    with events.subscribe(1) as queue:
        await reply_with_instant_template(
            model, context_for(), 0.0, events, 1, template_event, lambda _: False, tracker
        )
        [template, retraction] = [queue.get_nowait() for _ in range(queue.qsize())]
    assert isinstance(template, CoachCommentEvent) and isinstance(retraction, CoachCommentEvent)
    assert (retraction.provisional, retraction.retracted, retraction.text) == (True, True, "")
    assert retraction.message_key == template.message_key == "key"
    assert tracker._recorded == 0  # a discarded reply is not a latency sample


async def test_a_failure_or_cancellation_also_retracts() -> None:
    events = GameEvents()
    for failure in (RuntimeError("boom"), asyncio.CancelledError()):
        model = ScriptedLanguageModel(USAGE)
        model.queue_structured(GOOD)

        def settle(_: GroundedReply, failure: BaseException = failure) -> bool:
            raise failure

        with events.subscribe(1) as queue, pytest.raises(type(failure)):
            await reply_with_instant_template(
                model, context_for(), 0.0, events, 1, template_event, settle, LatencyTracker()
            )
        assert [e.retracted for e in drained_queue(queue)] == [False, True]


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
        await reply_with_instant_template(
            model, context_for(), 0.0, GameEvents(), 1, template_event, lambda _: True, tracker
        )

    assert "coach latency template p50=" in caplog.text
