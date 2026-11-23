import chess
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from chess_tutor.analysis.classification import DEFAULT_THRESHOLDS, assess_played_move
from chess_tutor.engines.stockfish import StockfishPool
from chess_tutor.models import Move
from chess_tutor.settings import ClassificationThresholds


async def classify_stored_move(
    engine: Engine,
    pool: StockfishPool,
    game_id: int,
    ply: int,
    thresholds: ClassificationThresholds = DEFAULT_THRESHOLDS,
) -> Move:
    """Classify a played move and store the label and the mover's win probabilities on it."""
    with Session(engine, expire_on_commit=False) as session:
        stored = session.scalars(select(Move).where(Move.game_id == game_id, Move.ply == ply)).one()
        assessment = await assess_played_move(
            pool, chess.Board(stored.fen_before), chess.Move.from_uci(stored.uci), thresholds
        )
        stored.classification = assessment.classification.value
        stored.eval_before = assessment.win_probability_before
        stored.eval_after = assessment.win_probability_after
        session.commit()
        return stored
