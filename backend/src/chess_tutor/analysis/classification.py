import math
from dataclasses import dataclass
from enum import StrEnum

import chess
import chess.engine

from chess_tutor.engines.stockfish import AnalysedLine, Budget, StockfishPool
from chess_tutor.facts.detectors import exchange_gain, piece_value
from chess_tutor.settings import ClassificationThresholds

DEFAULT_THRESHOLDS = ClassificationThresholds()
EXPLORED_LINES = 3
PAWN_VALUE = 1


class MoveClassification(StrEnum):
    BRILLIANT = "brilliant"
    GREAT = "great"
    BEST = "best"
    GOOD = "good"
    INACCURACY = "inaccuracy"
    MISTAKE = "mistake"
    BLUNDER = "blunder"


@dataclass(frozen=True)
class MoveAssessment:
    classification: MoveClassification
    win_probability_before: float  # the mover's chance, in percent, before the move
    win_probability_after: float

    @property
    def loss(self) -> float:
        return max(0.0, self.win_probability_before - self.win_probability_after)


def win_probability(score: chess.engine.Score) -> float:
    """Percent chance (0-100) that the side the score is from wins.

    Lichess's logistic: https://github.com/lichess-org/lila/blob/master/modules/analyse/src/main/WinPercent.scala
    (`50 + 50 * (2 / (1 + exp(-0.00368208 * cp)) - 1)`); mate is a certain win or loss.
    """
    if score.is_mate():
        return 100.0 if score > chess.engine.Cp(0) else 0.0
    centipawns = score.score()
    assert centipawns is not None
    return 50 + 50 * (2 / (1 + math.exp(-0.00368208 * centipawns)) - 1)


def move_accuracy(loss: float) -> float:
    """Lichess's per-move accuracy: https://github.com/lichess-org/lila/blob/master/modules/analyse/src/main/AccuracyPercent.scala"""
    return min(
        100.0,
        max(0.0, 103.1668100711649 * math.exp(-0.04354415386753951 * loss) - 3.166924740191411),
    )


def game_accuracy(win_probability_losses: list[float]) -> float:
    """Mean of the arithmetic and harmonic means of per-move accuracy (as lichess does).

    shortcut: lichess also weights the arithmetic mean by how volatile each position is; this
    uses the plain mean, add the weighting if the numbers must match lichess exactly.
    """
    if not win_probability_losses:
        return 100.0
    accuracies = [move_accuracy(loss) for loss in win_probability_losses]
    arithmetic = sum(accuracies) / len(accuracies)
    # The harmonic mean is undefined at 0, so floor each accuracy at a hundredth of a percent.
    harmonic = len(accuracies) / sum(1 / max(accuracy, 0.01) for accuracy in accuracies)
    return (arithmetic + harmonic) / 2


def _mover_win_probability(line: AnalysedLine, mover: chess.Color) -> float:
    return win_probability(line.white_score if mover == chess.WHITE else -line.white_score)


def _captured_value(board: chess.Board, move: chess.Move) -> int:
    if board.is_en_passant(move):
        return PAWN_VALUE
    return piece_value(board, move.to_square) if board.is_capture(move) else 0


def _material_sacrificed(board_before: chess.Board, move: chess.Move) -> int:
    """Most material the opponent nets by static exchange on any of the mover's pieces."""
    mover = board_before.turn
    after = board_before.copy(stack=False)
    after.push(move)
    worst = max(
        (
            exchange_gain(after, square, not mover)
            for square, piece in after.piece_map().items()
            if piece.color == mover and piece.piece_type != chess.KING
        ),
        default=0,
    )
    return worst - _captured_value(board_before, move)


def assess_move(
    before: list[AnalysedLine],
    board_before: chess.Board,
    move: chess.Move,
    thresholds: ClassificationThresholds = DEFAULT_THRESHOLDS,
    after: AnalysedLine | None = None,
) -> MoveAssessment:
    """Classify `move` from the analysis of `board_before` (best line first, MultiPV >= 2 to
    detect only-moves). `after` is the analysis of the resulting position, needed only when the
    move is not among the analysed lines."""
    mover = board_before.turn
    best_probability = _mover_win_probability(before[0], mover)
    played_line = next((line for line in before if line.pv and line.pv[0] == move), None)
    if played_line is not None:
        played_probability = _mover_win_probability(played_line, mover)
    elif after is not None:
        played_probability = _mover_win_probability(after, mover)
    else:
        raise ValueError("the move is not among the analysed lines; supply the analysis after it")
    # Measured against the best line, so an engine that scores a non-best line higher is no gain.
    loss = max(0.0, best_probability - played_probability)
    is_best_move = bool(before[0].pv) and before[0].pv[0] == move

    classification = _classify(
        loss, is_best_move, before, board_before, move, played_probability, thresholds
    )
    return MoveAssessment(classification, best_probability, played_probability)


def _classify(
    loss: float,
    is_best_move: bool,
    before: list[AnalysedLine],
    board_before: chess.Board,
    move: chess.Move,
    played_probability: float,
    thresholds: ClassificationThresholds,
) -> MoveClassification:
    if loss >= thresholds.blunder:
        return MoveClassification.BLUNDER
    if loss >= thresholds.mistake:
        return MoveClassification.MISTAKE
    if loss >= thresholds.inaccuracy:
        return MoveClassification.INACCURACY
    if (
        loss < thresholds.brilliant_max_loss
        and played_probability >= thresholds.brilliant_min_win_probability
        and _material_sacrificed(board_before, move) >= thresholds.brilliant_min_sacrifice
    ):
        return MoveClassification.BRILLIANT
    if is_best_move and len(before) >= 2:
        mover = board_before.turn
        gap = _mover_win_probability(before[0], mover) - _mover_win_probability(before[1], mover)
        if gap >= thresholds.great_alternative_gap:
            return MoveClassification.GREAT
    if is_best_move or loss < thresholds.best_loss:
        return MoveClassification.BEST
    return MoveClassification.GOOD


def classify_move(
    before: list[AnalysedLine],
    board_before: chess.Board,
    move: chess.Move,
    thresholds: ClassificationThresholds = DEFAULT_THRESHOLDS,
    after: AnalysedLine | None = None,
) -> MoveClassification:
    return assess_move(before, board_before, move, thresholds, after).classification


async def assess_played_move(
    pool: StockfishPool,
    board_before: chess.Board,
    move: chess.Move,
    thresholds: ClassificationThresholds = DEFAULT_THRESHOLDS,
    budget: Budget = Budget.STANDARD,
) -> MoveAssessment:
    """Analyse the position (MultiPV 3), and the position after the move if it is not a top line."""
    before = await pool.analyse(board_before, budget, multipv=EXPLORED_LINES)
    after: AnalysedLine | None = None
    if not any(line.pv and line.pv[0] == move for line in before):
        board_after = board_before.copy(stack=False)
        board_after.push(move)
        after = await _analyse_after(pool, board_after, budget)
    return assess_move(before, board_before, move, thresholds, after)


async def _analyse_after(pool: StockfishPool, board: chess.Board, budget: Budget) -> AnalysedLine:
    # The engine reports nothing for a finished game, so score it directly.
    if board.is_game_over():
        winner = board.outcome()
        assert winner is not None
        white_score: chess.engine.Score = chess.engine.Cp(0)
        if winner.winner is not None:
            white_score = (
                chess.engine.MateGiven if winner.winner == chess.WHITE else -chess.engine.MateGiven
            )
        return AnalysedLine(white_score=white_score, side_to_move_score=white_score, pv=[])
    return (await pool.analyse(board, budget))[0]
