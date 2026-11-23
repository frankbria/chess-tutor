import chess
import pytest

from chess_tutor.openings import PHASE_FOCUS, GamePhase, Opening, detect_phase, lookup_opening
from chess_tutor.settings import PhaseThresholds

THRESHOLDS = PhaseThresholds()


def board_after(*san_moves: str) -> chess.Board:
    board = chess.Board()
    for san in san_moves:
        board.push_san(san)
    return board


@pytest.mark.parametrize(
    ("moves", "eco", "name"),
    [
        (("e4", "e5", "Nf3", "Nc6", "Bb5"), "C60", "Ruy Lopez"),
        (("e4", "c5"), "B20", "Sicilian Defense"),
        (("d4", "d5", "c4"), "D06", "Queen's Gambit"),
    ],
)
def test_known_openings(moves: tuple[str, ...], eco: str, name: str) -> None:
    assert lookup_opening(board_after(*moves)) == Opening(eco, name)


def test_start_position_and_unnamed_game_have_no_opening() -> None:
    assert lookup_opening(chess.Board()) is None
    assert lookup_opening(chess.Board("8/8/8/8/8/8/8/K6k w - - 0 1")) is None


def test_transposition_reaches_the_same_name() -> None:
    assert lookup_opening(board_after("Nf3", "d5", "d4", "Nf6", "c4")) is not None


def test_opening_name_survives_leaving_book() -> None:
    in_book = lookup_opening(board_after("e4", "e5", "Nf3", "Nc6", "Bb5"))
    out_of_book = lookup_opening(
        board_after("e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Ba4", "Nf6", "O-O", "Be7", "h3", "h6")
    )
    assert in_book is not None and out_of_book is not None
    assert out_of_book.name.startswith("Ruy Lopez")


def test_opening_phase_while_in_book() -> None:
    assert (
        detect_phase(board_after("e4", "e5", "Nf3", "Nc6", "Bb5"), THRESHOLDS) is GamePhase.OPENING
    )


def test_opening_phase_when_out_of_book_but_undeveloped() -> None:
    board = board_after("a3", "h6", "h3", "a6")
    assert detect_phase(board, THRESHOLDS) is GamePhase.OPENING


def test_middlegame_once_developed_out_of_book() -> None:
    board = board_after("a3", "h6", "Nf3", "a6", "Nc3", "Nf6", "e3", "e6", "Bd3", "Bd6")
    assert detect_phase(board, THRESHOLDS) is GamePhase.MIDDLEGAME


def test_move_limit_ends_the_opening_even_if_undeveloped() -> None:
    board = chess.Board("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 13")
    assert detect_phase(board, THRESHOLDS) is GamePhase.MIDDLEGAME
    assert (
        detect_phase(
            chess.Board("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 12"), THRESHOLDS
        )
        is GamePhase.OPENING
    )


def test_early_queen_trade_with_full_minor_material_is_not_an_endgame() -> None:
    board = chess.Board("rnb1kbnr/ppp1pppp/8/8/8/8/PPP1PPPP/RNB1KBNR w KQkq - 0 4")
    assert detect_phase(board, THRESHOLDS) is GamePhase.OPENING


def test_queen_trade_with_little_material_left_is_an_endgame() -> None:
    board = chess.Board("r3k2r/pp3ppp/2n5/8/8/2N5/PP3PPP/R3K2R w KQkq - 0 20")
    assert detect_phase(board, THRESHOLDS) is GamePhase.ENDGAME


def test_rook_ending_is_an_endgame() -> None:
    board = chess.Board("4k3/pp3ppp/8/8/8/8/PP3PPP/R3K2r w - - 0 30")
    assert detect_phase(board, THRESHOLDS) is GamePhase.ENDGAME


def test_endgame_boundary_on_total_material_with_queens_on() -> None:
    # Q + R + minor = 17 total non-pawn material: above 13, queens on, so middlegame
    middlegame = chess.Board("4k3/8/8/8/8/8/8/Q3K3 w - - 0 40")  # 9 only: endgame
    assert detect_phase(middlegame, THRESHOLDS) is GamePhase.ENDGAME
    busy = chess.Board("3qk3/pppp4/8/8/8/8/PPPP4/RN2K2Q w - - 0 40")  # 9+9+5+3 = 26
    assert detect_phase(busy, THRESHOLDS) is GamePhase.MIDDLEGAME
    assert (
        detect_phase(busy, PhaseThresholds(endgame_max_non_pawn_material=26)) is GamePhase.ENDGAME
    )


def test_every_phase_has_a_focus_list() -> None:
    assert all(PHASE_FOCUS[phase] for phase in GamePhase)


def test_in_book_position_is_opening_even_when_developed() -> None:
    board = board_after(
        *[
            "a3",
            "e5",
            "b3",
            "d5",
            "c3",
            "Nf6",
            "d3",
            "Nc6",
            "e3",
            "Bd6",
            "f3",
            "O-O",
            "g3",
        ]  # Hippopotamus Attack
    )
    assert lookup_opening(board) is not None
    assert detect_phase(board, PhaseThresholds(opening_max_move=0)) is GamePhase.OPENING
