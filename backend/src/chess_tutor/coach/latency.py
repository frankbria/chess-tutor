"""Coaching latency (spec 9): an instant template first, the grounded model reply after it.

Raw model tokens are never shown, because grounding validates whole replies. So the client first
gets the deterministic template for the same context, marked provisional, and the validated reply
replaces it under the same message key. Both delays are measured per coaching task.
"""

import logging
import math
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from chess_tutor.api.events import CoachCommentEvent, GameEvents
from chess_tutor.coach.grounding import GroundedReply, coach_reply_with_grounding, template_reply
from chess_tutor.coach.llm import LanguageModel
from chess_tutor.coach.prompt import CoachContext, CoachReply

logger = logging.getLogger(__name__)

WINDOW = 200
LOG_EVERY = 10


def _percentile(sorted_values: list[float], fraction: float) -> float:
    """Nearest rank."""
    return sorted_values[max(math.ceil(fraction * len(sorted_values)) - 1, 0)]


@dataclass
class LatencyTracker:
    """Rolling p50/p95 of the last `window` coaching tasks, logged every `log_every` tasks.

    shortcut: per process and in memory, so a restart empties it; export to a metrics system if
    the numbers must outlive the process.
    """

    log_every: int = LOG_EVERY
    window: int = WINDOW
    _to_template: deque[float] = field(init=False)
    _to_final: deque[float] = field(init=False)
    _recorded: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self._to_template = deque(maxlen=self.window)
        self._to_final = deque(maxlen=self.window)

    def record(self, to_template: float, to_final: float) -> None:
        self._to_template.append(to_template)
        self._to_final.append(to_final)
        self._recorded += 1
        if self._recorded % self.log_every == 0:
            logger.info(
                "coach latency template p50=%s p95=%s final p50=%s p95=%s n=%d",
                *self._summary(self._to_template),
                *self._summary(self._to_final),
                len(self._to_final),
            )

    @staticmethod
    def _summary(values: deque[float]) -> tuple[str, str]:
        ordered = sorted(values)
        return f"{_percentile(ordered, 0.5):.3f}s", f"{_percentile(ordered, 0.95):.3f}s"


COACH_LATENCY = LatencyTracker()


async def reply_with_instant_template(
    model: LanguageModel,
    context: CoachContext,
    started: float,
    events: GameEvents,
    game_id: int,
    template_event: Callable[[CoachReply], CoachCommentEvent],
    settle: Callable[[GroundedReply], bool],
    tracker: LatencyTracker = COACH_LATENCY,
) -> None:
    """Publish the template at once, then `settle` the grounded reply once it is ready.

    `settle` stores and publishes the final event and says whether it did; when it did not (the
    move was taken back, the proposal went stale) or anything failed, the template is retracted.
    `started` is a `time.monotonic()` reading from when the coaching task began.
    """
    provisional = template_event(template_reply(context))
    events.publish(game_id, provisional)
    to_template = time.monotonic() - started
    settled = False
    try:
        settled = settle(await coach_reply_with_grounding(model, context))
        if settled:
            tracker.record(to_template, time.monotonic() - started)
    finally:
        if not settled:
            retraction = provisional.model_copy(
                update={"text": "", "fact_ids": [], "retracted": True}
            )
            events.publish(game_id, retraction)
