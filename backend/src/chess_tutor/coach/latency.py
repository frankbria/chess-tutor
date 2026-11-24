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
    show_template: Callable[[CoachReply], None],
    tracker: LatencyTracker = COACH_LATENCY,
) -> GroundedReply:
    """Hand the template to `show_template` at once, then wait for the grounded reply.

    `started` is a `time.monotonic()` reading from when the coaching task began.
    """
    show_template(template_reply(context))
    to_template = time.monotonic() - started
    reply = await coach_reply_with_grounding(model, context)
    tracker.record(to_template, time.monotonic() - started)
    return reply
