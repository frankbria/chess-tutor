import pytest

from chess_tutor.engines.calibration import adjudicate, elo_from_score, estimate_elo


def test_an_even_score_is_zero_elo() -> None:
    assert elo_from_score(0.5) == 0


def test_a_64_percent_score_is_about_100_elo() -> None:
    assert elo_from_score(0.64) == pytest.approx(100, abs=2)


def test_a_perfect_score_is_clamped_not_infinite() -> None:
    assert elo_from_score(1.0) < 1500


def test_the_interval_brackets_the_estimate_and_narrows_with_more_games() -> None:
    few = estimate_elo([1.0, 0.0, 0.5, 1.0, 0.0, 0.5, 1.0, 0.0])
    many = estimate_elo([1.0, 0.0, 0.5, 1.0, 0.0, 0.5, 1.0, 0.0] * 20)
    assert few.lower < few.elo_difference < few.upper
    assert many.upper - many.lower < (few.upper - few.lower) / 2
    assert few.score == many.score == 0.5


def test_a_lopsided_run_gives_a_positive_difference() -> None:
    assert estimate_elo([1.0] * 6 + [0.5] * 2 + [0.0] * 2).elo_difference > 0


def test_one_game_has_no_interval() -> None:
    with pytest.raises(ValueError):
        estimate_elo([1.0])


@pytest.mark.parametrize(
    ("centipawns", "expected"),
    [(400, 1.0), (150, 1.0), (149, 0.5), (0, 0.5), (-149, 0.5), (-150, 0.0)],
)
def test_adjudication_calls_only_decisive_evals(centipawns: int, expected: float) -> None:
    assert adjudicate(centipawns) == expected


def test_all_wins_still_has_an_interval_with_a_finite_lower_bound() -> None:
    estimate = estimate_elo([1.0] * 20)
    assert 0 < estimate.lower < estimate.elo_difference <= estimate.upper


def test_all_losses_still_has_an_interval_with_a_finite_upper_bound() -> None:
    estimate = estimate_elo([0.0] * 20)
    assert estimate.lower <= estimate.elo_difference < estimate.upper < 0


def test_all_draws_straddle_zero() -> None:
    estimate = estimate_elo([0.5] * 20)
    assert estimate.lower < 0 < estimate.upper
