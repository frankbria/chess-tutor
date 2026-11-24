from sqlalchemy import select
from sqlalchemy.orm import Session

from chess_tutor.models import Game, GameMode, GameResult, Player, PlayerColor, RatingHistory
from chess_tutor.rating.glicko2 import GameOutcome, Rating, weighted_update
from chess_tutor.settings import RatingWeights, Thresholds


def mode_weight(weights: RatingWeights, mode: GameMode) -> float:
    return {
        GameMode.REGULAR: weights.regular,
        GameMode.PASSIVE: weights.passive_tutor,
        GameMode.ACTIVE: weights.active_tutor,
    }[mode]


def takeback_multiplier(weights: RatingWeights, takebacks: int) -> float:
    return max(weights.takeback_floor, 1 - weights.takeback_penalty * takebacks)


def player_score(game: Game) -> float:
    if game.result is GameResult.DRAW:
        return 0.5
    player_won = (game.result is GameResult.WHITE_WINS) == (game.player_color is PlayerColor.WHITE)
    return 1.0 if player_won else 0.0


def record_rating(session: Session, game: Game, thresholds: Thresholds) -> None:
    """Apply a finished game to the player's rating, once; later calls for the game do nothing."""
    session.flush()
    if session.scalar(select(RatingHistory.id).where(RatingHistory.game_id == game.id)):
        return
    player = session.get_one(Player, game.player_id)
    weights = thresholds.rating_weights
    weight = mode_weight(weights, game.mode) * takeback_multiplier(weights, game.takeback_count)
    before = Rating(player.rating, player.rating_deviation, player.volatility)
    outcome = GameOutcome(
        game.opponent_elo, thresholds.opponent_rating_deviation, player_score(game)
    )
    after = weighted_update(before, [outcome], weight, thresholds.rating_system_constant)
    player.rating, player.rating_deviation, player.volatility = (
        after.rating,
        after.deviation,
        after.volatility,
    )
    session.add(
        RatingHistory(
            player_id=player.id,
            game_id=game.id,
            rating_before=before.rating,
            rating_after=after.rating,
            deviation_before=before.deviation,
            deviation_after=after.deviation,
            weight=weight,
        )
    )
