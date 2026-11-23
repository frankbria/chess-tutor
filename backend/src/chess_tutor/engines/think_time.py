import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import Protocol

import chess

from chess_tutor.engines.human_moves import HumanMovePrediction
from chess_tutor.settings import Settings

logger = logging.getLogger(__name__)

OBVIOUS_TOP_PROBABILITY = 0.9  # at or above this one move is clear: think the minimum
SPREAD_TOP_PROBABILITY = 0.15  # at or below this the policy is spread: think the maximum
JITTER_FRACTION = 0.1  # of the min-to-max range, either way


class HumanMoveSource(Protocol):
    async def predict_human_moves(
        self, board: chess.Board, elo: int, k: int
    ) -> list[HumanMovePrediction]: ...


Sleeper = Callable[[float], Awaitable[None]]


def think_time_seconds(
    top_probability: float, settings: Settings, rng: random.Random | None = None
) -> float:
    """Linear in the top move's probability (obvious -> min, spread -> max), plus bounded jitter."""
    minimum = settings.opponent_min_think_seconds
    maximum = settings.opponent_max_think_seconds
    span = OBVIOUS_TOP_PROBABILITY - SPREAD_TOP_PROBABILITY
    difficulty = (OBVIOUS_TOP_PROBABILITY - top_probability) / span
    base = minimum + (maximum - minimum) * min(max(difficulty, 0.0), 1.0)
    jitter = (rng or random).uniform(-1, 1) * JITTER_FRACTION * (maximum - minimum)
    return min(max(base + jitter, minimum), maximum)


class OpponentPacing:
    """Waits a humanised time before the opponent plays; never blocks the game on the predictor."""

    def __init__(
        self,
        settings: Settings,
        predictor: HumanMoveSource | None = None,
        sleeper: Sleeper = asyncio.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self._settings = settings
        self._predictor = predictor
        self._sleeper = sleeper
        self._rng = rng

    async def pause(self, board: chess.Board, elo: int) -> None:
        await self._sleeper(await self._delay(board, elo))

    async def _delay(self, board: chess.Board, elo: int) -> float:
        if self._predictor is not None:
            try:
                predictions = await self._predictor.predict_human_moves(board, elo, 1)
                return think_time_seconds(predictions[0].probability, self._settings, self._rng)
            # The helper is an external process: any failure falls back to the minimum.
            except Exception:
                logger.warning("think-time prediction failed; using the minimum", exc_info=True)
        return self._settings.opponent_min_think_seconds
