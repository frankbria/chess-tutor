"""Score statistics and adjudication for the steering self-play calibration."""

import math
from collections.abc import Sequence
from dataclasses import dataclass

NORMAL_95 = 1.959964  # two-sided 95% standard normal quantile
ADJUDICATION_THRESHOLD_CENTIPAWNS = 150


@dataclass(frozen=True)
class EloEstimate:
    score: float
    elo_difference: float
    lower: float
    upper: float


def adjudicate(white_centipawns: int, threshold: int = ADJUDICATION_THRESHOLD_CENTIPAWNS) -> float:
    """White's result for a game cut short: a decisive eval is a win, anything else a draw."""
    if white_centipawns >= threshold:
        return 1.0
    if white_centipawns <= -threshold:
        return 0.0
    return 0.5


def elo_from_score(score: float) -> float:
    """Logistic Elo difference; a perfect or zero score is clamped to half a game's worth."""
    bounded = min(max(score, 1e-3), 1 - 1e-3)
    return -400 * math.log10(1 / bounded - 1)


def estimate_elo(results: Sequence[float]) -> EloEstimate:
    """Elo difference of the subject and its 95% interval from per-game scores (1, 0.5, 0)."""
    games = len(results)
    if games < 2:
        raise ValueError("at least two games are needed for a confidence interval")
    score = sum(results) / games
    # Wilson interval on the score, with draws counted as half wins: unlike the sample variance
    # it stays wide when every result is identical. Slightly conservative when draws are common.
    z_squared = NORMAL_95**2
    centre = (score + z_squared / (2 * games)) / (1 + z_squared / games)
    margin = (
        NORMAL_95
        * math.sqrt(score * (1 - score) / games + z_squared / (4 * games**2))
        / (1 + z_squared / games)
    )
    return EloEstimate(
        score,
        elo_from_score(score),
        elo_from_score(centre - margin),
        elo_from_score(centre + margin),
    )
