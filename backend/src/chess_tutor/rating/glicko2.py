"""Glicko-2 (Glickman, "Example of the Glicko-2 system") for one rating period."""

import math
from dataclasses import dataclass

SCALE = 173.7178  # converts between the Glicko rating scale and the internal Glicko-2 scale
BASE_RATING = 1500.0
CONVERGENCE_TOLERANCE = 1e-6
DEFAULT_SYSTEM_CONSTANT = 0.5


@dataclass(frozen=True)
class Rating:
    rating: float
    deviation: float
    volatility: float


@dataclass(frozen=True)
class GameOutcome:
    opponent_rating: float
    opponent_deviation: float
    score: float  # 1 win, 0.5 draw, 0 loss


def _g(deviation: float) -> float:
    return 1 / math.sqrt(1 + 3 * deviation**2 / math.pi**2)


def _expected(mu: float, opponent_mu: float, opponent_phi: float) -> float:
    return 1 / (1 + math.exp(-_g(opponent_phi) * (mu - opponent_mu)))


def _new_volatility(
    phi: float, sigma: float, delta: float, variance: float, system_constant: float
) -> float:
    a = math.log(sigma**2)

    def f(x: float) -> float:
        exp_x = math.exp(x)
        denominator = phi**2 + variance + exp_x
        return (
            exp_x * (delta**2 - denominator) / (2 * denominator**2) - (x - a) / system_constant**2
        )

    start = a
    if delta**2 > phi**2 + variance:
        end = math.log(delta**2 - phi**2 - variance)
    else:
        step = 1
        while f(a - step * system_constant) < 0:
            step += 1
        end = a - step * system_constant
    f_start, f_end = f(start), f(end)
    while abs(end - start) > CONVERGENCE_TOLERANCE:
        midpoint = start + (start - end) * f_start / (f_end - f_start)
        f_midpoint = f(midpoint)
        if f_midpoint * f_end <= 0:
            start, f_start = end, f_end
        else:
            f_start /= 2
        end, f_end = midpoint, f_midpoint
    return math.exp(start / 2)


def update(
    player: Rating, games: list[GameOutcome], system_constant: float = DEFAULT_SYSTEM_CONSTANT
) -> Rating:
    mu = (player.rating - BASE_RATING) / SCALE
    phi = player.deviation / SCALE
    if not games:  # no games in the period: only the deviation grows
        grown = math.sqrt(phi**2 + player.volatility**2)
        return Rating(player.rating, grown * SCALE, player.volatility)
    variance_inverse = 0.0
    score_sum = 0.0
    for game in games:
        opponent_mu = (game.opponent_rating - BASE_RATING) / SCALE
        opponent_phi = game.opponent_deviation / SCALE
        expected = _expected(mu, opponent_mu, opponent_phi)
        variance_inverse += _g(opponent_phi) ** 2 * expected * (1 - expected)
        score_sum += _g(opponent_phi) * (game.score - expected)
    variance = 1 / variance_inverse
    delta = variance * score_sum
    sigma = _new_volatility(phi, player.volatility, delta, variance, system_constant)
    phi_star = math.sqrt(phi**2 + sigma**2)
    new_phi = 1 / math.sqrt(1 / phi_star**2 + 1 / variance)
    new_mu = mu + new_phi**2 * score_sum
    return Rating(new_mu * SCALE + BASE_RATING, new_phi * SCALE, sigma)


def weighted_update(
    player: Rating,
    games: list[GameOutcome],
    weight: float,
    system_constant: float = DEFAULT_SYSTEM_CONSTANT,
) -> Rating:
    """Glicko-2 update scaled by `weight`: new = old + weight * (glicko_new - old).

    Rating, deviation and volatility are all blended this way, so a tutor game moves each less.
    """
    full = update(player, games, system_constant)
    return Rating(
        player.rating + weight * (full.rating - player.rating),
        player.deviation + weight * (full.deviation - player.deviation),
        player.volatility + weight * (full.volatility - player.volatility),
    )
