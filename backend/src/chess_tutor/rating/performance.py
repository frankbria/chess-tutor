from sqlalchemy import select
from sqlalchemy.orm import Session

from chess_tutor.analysis.classification import game_accuracy
from chess_tutor.models import Game, Move, PlayerColor

BASELINE_ACCURACY = 75.0
ELO_PER_ACCURACY_POINT = 15.0
MINIMUM_PERFORMANCE = 400
MAXIMUM_PERFORMANCE = 3000


def performance_rating(opponent_elo: int, accuracy: float) -> float:
    """Opponent Elo shifted by how far the player's accuracy is from the baseline.

    shortcut: neither lichess nor chess.com publishes an accuracy-to-rating relation, so this is
    a transparent linear guess (about 15 Elo per accuracy point around 75%); calibrate it against
    stored games once there are enough.
    """
    estimate = opponent_elo + ELO_PER_ACCURACY_POINT * (accuracy - BASELINE_ACCURACY)
    return min(float(MAXIMUM_PERFORMANCE), max(float(MINIMUM_PERFORMANCE), estimate))


def record_performance(session: Session, game: Game) -> None:
    """Store accuracy and performance; both stay null when no move of the player is classified."""
    player_parity = 1 if game.player_color is PlayerColor.WHITE else 0
    moves = session.scalars(select(Move).where(Move.game_id == game.id))
    losses = [
        max(0.0, move.eval_before - move.eval_after)
        for move in moves
        if move.ply % 2 == player_parity
        and move.eval_before is not None
        and move.eval_after is not None
    ]
    if not losses:
        return
    game.accuracy = game_accuracy(losses)
    game.performance_rating = performance_rating(game.opponent_elo, game.accuracy)
