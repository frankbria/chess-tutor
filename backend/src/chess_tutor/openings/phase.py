from enum import StrEnum

import chess

from chess_tutor.openings.book import is_in_book
from chess_tutor.settings import PhaseThresholds

PIECE_VALUES = {chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}
MINOR_HOME_SQUARES = {
    chess.WHITE: (chess.B1, chess.G1, chess.C1, chess.F1),
    chess.BLACK: (chess.B8, chess.G8, chess.C8, chess.F8),
}
UNDEVELOPED_MINORS_FOR_OPENING = 2


class GamePhase(StrEnum):
    OPENING = "opening"
    MIDDLEGAME = "middlegame"
    ENDGAME = "endgame"


PHASE_FOCUS: dict[GamePhase, list[str]] = {
    GamePhase.OPENING: [
        "development",
        "centre control",
        "king safety and castling",
        "not moving a piece twice",
        "the named opening and its typical plans",
    ],
    GamePhase.MIDDLEGAME: [
        "tactics",
        "plans",
        "weak squares",
        "piece activity",
        "trades",
    ],
    GamePhase.ENDGAME: [
        "king activity",
        "passed pawns",
        "opposition",
        "rook placement",
        "converting material",
    ],
}


def non_pawn_material(board: chess.Board) -> int:
    return sum(
        value * len(board.pieces(piece_type, color))
        for piece_type, value in PIECE_VALUES.items()
        for color in chess.COLORS
    )


def _undeveloped_minor_count(board: chess.Board) -> int:
    minors = board.knights | board.bishops
    return sum(
        1
        for square in MINOR_HOME_SQUARES[board.turn]
        if minors & chess.BB_SQUARES[square] and board.color_at(square) == board.turn
    )


def detect_phase(board: chess.Board, thresholds: PhaseThresholds) -> GamePhase:
    material = non_pawn_material(board)
    queens_off = not board.queens
    if material <= thresholds.endgame_max_non_pawn_material or (
        queens_off and material <= thresholds.queens_off_max_non_pawn_material
    ):
        return GamePhase.ENDGAME
    still_developing = (
        board.fullmove_number <= thresholds.opening_max_move
        and _undeveloped_minor_count(board) >= UNDEVELOPED_MINORS_FOR_OPENING
    )
    if is_in_book(board) or still_developing:
        return GamePhase.OPENING
    return GamePhase.MIDDLEGAME
