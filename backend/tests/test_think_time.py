import random

import pytest

from chess_tutor.engines.think_time import JITTER_FRACTION, think_time_seconds
from chess_tutor.settings import Settings

SETTINGS = Settings(
    _env_file=None,
    llm_provider="none",
    opponent_min_think_seconds=1.0,
    opponent_max_think_seconds=5.0,
)
MAX_JITTER = JITTER_FRACTION * (5.0 - 1.0)


def delay(top_probability: float, seed: int = 0) -> float:
    return think_time_seconds(top_probability, SETTINGS, random.Random(seed))


def test_obvious_move_thinks_near_the_minimum() -> None:
    assert 1.0 <= delay(0.95) <= 1.0 + MAX_JITTER


def test_spread_policy_thinks_near_the_maximum() -> None:
    assert 5.0 - MAX_JITTER <= delay(0.15) <= 5.0


def test_delay_falls_as_the_top_probability_rises() -> None:
    class Midpoint(random.Random):
        def uniform(self, a: float, b: float) -> float:
            return 0.0

    delays = [
        think_time_seconds(probability, SETTINGS, Midpoint())
        for probability in (0.05, 0.15, 0.3, 0.5, 0.7, 0.9, 1.0)
    ]

    assert delays == sorted(delays, reverse=True)
    assert delays[0] > delays[-1]


@pytest.mark.parametrize("probability", [0.0, 0.1, 0.4, 0.6, 0.9, 1.0])
@pytest.mark.parametrize("seed", range(20))
def test_delay_stays_within_the_configured_bounds(probability: float, seed: int) -> None:
    assert 1.0 <= delay(probability, seed) <= 5.0


def test_jitter_varies_the_delay_within_its_bound() -> None:
    delays = {delay(0.5, seed) for seed in range(20)}

    assert len(delays) > 1
    assert max(delays) - min(delays) <= 2 * MAX_JITTER


def test_zero_bounds_give_zero_delay() -> None:
    zero = Settings(
        _env_file=None,
        llm_provider="none",
        opponent_min_think_seconds=0,
        opponent_max_think_seconds=0,
    )

    assert think_time_seconds(0.3, zero) == 0


def test_min_above_max_is_rejected() -> None:
    with pytest.raises(ValueError, match="opponent_min_think_seconds"):
        Settings(
            _env_file=None,
            llm_provider="none",
            opponent_min_think_seconds=5,
            opponent_max_think_seconds=1,
        )
