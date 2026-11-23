import chess
import pytest

from chess_tutor.facts import Fact, FactType, build_fact_packet
from chess_tutor.facts.positional import (
    activity,
    development,
    king_safety,
    pawn_structure,
    positional_facts,
)
from chess_tutor.settings import PhaseThresholds

START = chess.STARTING_FEN
QUIET_OPENING = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"
SOLID_CASTLED = "r1bq1rk1/ppp2ppp/2n2n2/3p4/3P4/2NB1N2/PPP2PPP/R1BQ1RK1 w - - 0 8"
UNCASTLED = "r1bqk2r/ppp2ppp/2n2n2/8/2BP4/2N2N2/PPP2PPP/R1BQK2R w KQkq - 0 10"
NO_G_PAWN = "r1bq1rk1/ppp2ppp/2n2n2/3p4/3P4/2N2N2/PPP2P1P/R1BQ1RK1 w - - 0 8"


def board_after(moves: str) -> chess.Board:
    board = chess.Board()
    for san in moves.split():
        board.push_san(san)
    return board


def ids(facts: list[Fact]) -> set[str]:
    return {fact.id for fact in facts}


def summaries(facts: list[Fact]) -> str:
    return " | ".join(fact.summary for fact in facts)


def test_uncastled_king_with_open_centre_is_flagged() -> None:
    facts = king_safety(chess.Board(UNCASTLED))
    assert ids(facts) == {"king_safety:white", "king_safety:black"}
    assert "uncastled in the centre" in summaries(facts)


def test_missing_shield_pawn_is_flagged() -> None:
    facts = king_safety(chess.Board(NO_G_PAWN))
    assert ids(facts) == {"king_safety:white"}
    assert "no pawn on the g-file" in facts[0].summary
    assert facts[0].squares == ["g1"]


def test_half_open_file_next_to_the_king_is_flagged() -> None:
    # White castled long and traded the d-pawn for black's c-pawn.
    facts = king_safety(board_after("e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 a6 Be3 e5 Nb3 Be6"))
    assert "king_safety:white" in ids(facts)
    assert "d-file is half-open" in facts[0].summary


def test_king_safety_quiet_positions_have_no_facts() -> None:
    for fen in (START, QUIET_OPENING, SOLID_CASTLED):
        assert king_safety(chess.Board(fen)) == []


def test_king_safety_skipped_in_endgames() -> None:
    assert king_safety(chess.Board("4k3/8/8/8/8/8/8/4K2r w - - 0 40")) == []


def test_isolated_and_passed_pawns() -> None:
    facts = pawn_structure(chess.Board("4k3/8/8/8/8/8/P6P/4K3 w - - 0 1"))
    assert {"pawns:white:isolated:a2", "pawns:white:isolated:h2"} <= ids(facts)
    assert {"pawns:white:passed:a2", "pawns:white:passed:h2"} <= ids(facts)


def test_passed_pawn_is_flagged_with_its_rank() -> None:
    facts = pawn_structure(chess.Board("8/8/8/8/8/k7/6P1/4K3 w - - 0 1"))
    passed = next(fact for fact in facts if fact.id == "pawns:white:passed:g2")
    assert "rank 2" in passed.summary
    assert passed.severity == 1


def test_doubled_pawns_group_into_one_fact() -> None:
    facts = pawn_structure(chess.Board("4k3/8/8/8/8/2P5/2P2PPP/4K3 w - - 0 1"))
    doubled = next(fact for fact in facts if fact.id == "pawns:white:doubled:c")
    assert doubled.squares == ["c2", "c3"]


def test_backward_pawn() -> None:
    facts = pawn_structure(chess.Board("4k3/8/8/3p4/1P1P4/2P5/8/4K3 w - - 0 1"))
    assert "pawns:white:backward:c3" in ids(facts)
    assert "pawns:white:backward:b4" not in ids(facts)
    # a neighbour level with the pawn can still support it, so it is not backward
    supported = pawn_structure(chess.Board("4k3/8/8/3p4/3P4/1PP5/8/4K3 w - - 0 1"))
    assert "pawns:white:backward:c3" not in ids(supported)


def test_pawn_structure_quiet_positions_have_no_facts() -> None:
    assert pawn_structure(chess.Board(START)) == []
    assert pawn_structure(chess.Board(QUIET_OPENING)) == []


def test_undeveloped_minor_pieces_after_move_five() -> None:
    facts = development(chess.Board(QUIET_OPENING.replace("2 3", "2 5")))
    assert {"development:white", "development:black"} <= ids(facts)
    assert "undeveloped on b1, c1, f1" in facts[0].summary


def test_early_queen_is_flagged() -> None:
    facts = development(board_after("e4 e5 Qh5 Nc6"))
    assert ids(facts) == {"development:white"}
    assert "queen came out" in facts[0].summary


def test_piece_moved_twice_uses_the_move_stack() -> None:
    board = board_after("e4 e5 Nf3 Nc6 Bb5 a6 Ba4")
    assert "has moved more than once" in summaries(development(board))
    assert "moved more than once" not in summaries(development(chess.Board(board.fen())))


def test_centre_control_lead() -> None:
    facts = development(board_after("e4 c5 Nf3 d6 d4 cxd4"))
    centre = next(fact for fact in facts if fact.id == "development:centre")
    assert centre.side.value == "white"
    assert centre.squares == []


def test_development_quiet_positions_have_no_facts() -> None:
    assert development(chess.Board(START)) == []
    assert development(chess.Board(QUIET_OPENING)) == []
    assert development(chess.Board(SOLID_CASTLED)) == []


def test_development_respects_configured_opening_length() -> None:
    board = board_after("e4 e5 Qh5 Nc6")
    assert development(board, PhaseThresholds(opening_max_move=1)) == []


@pytest.mark.parametrize(
    ("fen", "expected"),
    [
        ("4k3/8/8/8/8/8/PP3PPP/3RK3 w - - 0 20", "activity:white:rook_file:d1"),
        ("4k3/3p4/8/8/8/8/PP3PPP/3RK3 w - - 0 20", "activity:white:rook_file:d1"),
        ("4k3/R7/8/8/8/8/8/4K3 w - - 0 20", "activity:white:rook_seventh:a7"),
        ("4k3/8/8/8/8/8/p3r3/4K3 b - - 0 20", "activity:black:rook_seventh:e2"),
        ("4k3/8/8/8/8/P1P1P1P1/1P1P1P1P/2B1K3 w - - 0 20", "activity:white:bad_bishop:c1"),
        ("2b1k3/1p1p1p1p/p1p1p1p1/8/8/8/8/4K3 b - - 0 20", "activity:black:bad_bishop:c8"),
        ("7k/8/8/8/8/8/5PPP/K7 w - - 0 40", "activity:white:passive_king"),
        ("7k/5ppp/8/8/8/8/8/K7 b - - 0 40", "activity:black:passive_king"),
    ],
)
def test_activity_positive(fen: str, expected: str) -> None:
    assert expected in ids(activity(chess.Board(fen)))


def test_half_open_file_is_labelled() -> None:
    facts = activity(chess.Board("4k3/3p4/8/8/8/8/PP3PPP/3RK3 w - - 0 20"))
    assert "half-open" in facts[0].summary


@pytest.mark.parametrize(
    "fen",
    [
        START,
        "8/8/8/3k4/8/4K3/8/8 w - - 0 40",
        "r1bq1rk1/ppp2ppp/2n2n2/3p4/3P4/2NB1N2/PPP2PPP/R1BQ1RK1 w - - 0 14",
    ],
)
def test_activity_quiet_positions_have_no_facts(fen: str) -> None:
    assert activity(chess.Board(fen)) == []


FIXTURE_BOARDS = [
    chess.Board(fen)
    for fen in (
        START,
        QUIET_OPENING,
        UNCASTLED,
        NO_G_PAWN,
        "4k3/8/8/8/8/2P5/2P2PPP/4K3 w - - 0 1",
        "4k3/8/8/3p4/1P1P4/2P5/8/4K3 w - - 0 1",
        "4k3/R7/8/8/8/8/8/4K3 w - - 0 20",
        "4k3/8/8/8/8/P1P1P1P1/1P1P1P1P/2B1K3 w - - 0 20",
        "7k/8/8/8/8/8/5PPP/K7 w - - 0 40",
    )
] + [
    board_after("e4 e5 Qh5 Nc6 Bc4 Nf6 Qxf7"),
    board_after("e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 a6 Be3 e5 Nb3 Be6"),
]


@pytest.mark.parametrize("board", FIXTURE_BOARDS, ids=lambda board: board.fen())
def test_squares_and_pieces_are_parallel_and_output_is_deterministic(board: chess.Board) -> None:
    facts = positional_facts(board)
    assert facts == positional_facts(board)
    assert build_fact_packet(board).to_compact_json() == build_fact_packet(board).to_compact_json()
    for fact in facts:
        assert [piece.square for piece in fact.pieces] == fact.squares
        assert fact.type in {
            FactType.KING_SAFETY,
            FactType.PAWN_STRUCTURE,
            FactType.DEVELOPMENT,
            FactType.ACTIVITY,
        }
    assert len({fact.id for fact in facts}) == len(facts)
