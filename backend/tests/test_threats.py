from collections.abc import Callable

import chess
import pytest

from chess_tutor.facts import (
    Fact,
    FactType,
    Side,
    back_rank_weaknesses,
    build_fact_packet,
    mate_threats,
    mates_in_one,
    overloaded_defenders,
    trapped_pieces,
)

START = chess.STARTING_FEN
OPENING = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"
QUIET_POSITIONS = [START, OPENING]
Detector = Callable[[chess.Board], list[Fact]]


def ids(detector: Detector, fen: str) -> set[str]:
    return {fact.id for fact in detector(chess.Board(fen))}


@pytest.mark.parametrize("fen", QUIET_POSITIONS)
@pytest.mark.parametrize(
    "detector",
    [back_rank_weaknesses, mate_threats, mates_in_one, overloaded_defenders, trapped_pieces],
)
def test_quiet_positions_have_none(fen: str, detector: Detector) -> None:
    assert detector(chess.Board(fen)) == []


@pytest.mark.parametrize(
    ("fen", "expected"),
    [
        ("r5k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1", {"back_rank:g1", "back_rank:g8"}),
        ("3r2k1/5ppp/8/8/8/8/5PPP/6K1 w - - 0 1", {"back_rank:g1"}),
        ("6k1/5ppp/8/8/8/8/5PPP/3Q2K1 w - - 0 1", {"back_rank:g8"}),
    ],
)
def test_back_rank_weakness(fen: str, expected: set[str]) -> None:
    assert ids(back_rank_weaknesses, fen) == expected


@pytest.mark.parametrize(
    "fen",
    [
        "3r2k1/5ppp/8/8/8/8/5PP1/6K1 w - - 0 1",  # luft on h2
        "2rr2k1/5ppp/8/8/8/8/5PPP/R2R2K1 w - - 0 1",  # both back ranks guarded twice
        "6k1/5ppp/8/8/8/8/5PPP/6K1 w - - 0 1",  # nobody to exploit it
    ],
)
def test_back_rank_not_reported(fen: str) -> None:
    assert back_rank_weaknesses(chess.Board(fen)) == []


@pytest.mark.parametrize(
    ("fen", "expected"),
    [
        ("3r2k1/5ppp/8/8/8/8/5PPP/6K1 w - - 0 1", "mate_threat:d8d1"),  # back-rank
        ("5rk1/5pp1/8/7Q/8/8/2B5/6K1 b - - 0 1", "mate_threat:h5h7"),  # Qh7#
        (
            "r1bqkbnr/pppp1ppp/2n5/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR b KQkq - 0 1",
            "mate_threat:h5f7",  # scholar's mate
        ),
    ],
)
def test_mate_threat(fen: str, expected: str) -> None:
    board = chess.Board(fen)
    fact = next(fact for fact in mate_threats(board) if fact.id == expected)
    assert fact.type is FactType.MATE_THREAT
    assert fact.side is not Side(("white", "black")[board.turn == chess.BLACK])
    assert fact.san_lines[0][0].endswith("#")


@pytest.mark.parametrize(
    "fen",
    [
        "3r2k1/5ppp/8/8/8/8/5PP1/6K1 w - - 0 1",  # luft, no mate
        "4k3/8/8/8/8/8/4r3/4K3 w - - 0 1",  # side to move is in check
        "3r2k1/5ppp/8/8/8/5n2/5PPP/6K1 w - - 0 1",  # in check, a null move would allow Rd1#
    ],
)
def test_no_mate_threat(fen: str) -> None:
    assert mate_threats(chess.Board(fen)) == []


@pytest.mark.parametrize(
    ("fen", "expected"),
    [
        ("3r2k1/5ppp/8/8/8/8/5PPP/6K1 b - - 0 1", "mate_in_one:d8d1"),
        ("5rk1/5pp1/8/7Q/8/8/2B5/6K1 w - - 0 1", "mate_in_one:h5h7"),
        ("r1bqkbnr/pppp1ppp/2n5/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 0 1", "mate_in_one:h5f7"),
    ],
)
def test_mate_in_one(fen: str, expected: str) -> None:
    fact = next(fact for fact in mates_in_one(chess.Board(fen)) if fact.id == expected)
    assert fact.type is FactType.MATE_IN_ONE
    assert fact.san_lines[0][0].endswith("#")


def test_no_mate_in_one_without_mate() -> None:
    assert mates_in_one(chess.Board("3r2k1/5ppp/8/8/8/8/5PP1/6K1 b - - 0 1")) == []


@pytest.mark.parametrize(
    ("fen", "defender", "victims"),
    [
        # queen alone guards the knight on c6 and the bishop on f5
        ("6k1/3q4/2n5/5b2/8/3B4/8/2R3K1 b - - 0 1", "d7", {"c6", "f5"}),
        # the same pattern for white
        ("2r3k1/8/3b4/8/5B2/2N5/3Q4/6K1 w - - 0 1", "d2", {"c3", "f4"}),
        # rook alone guards the bishop on c5 and the knight on d8
        ("2rn2k1/8/8/2b5/8/8/5B2/3Q2K1 b - - 0 1", "c8", {"c5", "d8"}),
    ],
)
def test_overloaded_defender(fen: str, defender: str, victims: set[str]) -> None:
    fact = next(
        fact
        for fact in overloaded_defenders(chess.Board(fen))
        if fact.id == f"overloaded:{defender}"
    )
    assert fact.type is FactType.OVERLOADED_DEFENDER
    assert set(fact.squares[1:]) == victims


@pytest.mark.parametrize(
    "fen",
    [
        "6k1/3q4/2n5/8/8/3B4/8/2R3K1 b - - 0 1",  # only one piece is attacked
        "6k1/3q4/2n2b2/8/8/3B4/8/2R3K1 b - - 0 1",  # the second piece is not attacked
        "6k1/3q4/2n3p1/5b2/8/3B4/8/2R3K1 b - - 0 1",  # a pawn also guards the bishop
    ],
)
def test_not_overloaded(fen: str) -> None:
    assert overloaded_defenders(chess.Board(fen)) == []


@pytest.mark.parametrize(
    ("fen", "trapped"),
    [
        ("r5k1/B1p5/1p6/8/8/8/8/6K1 w - - 0 1", "a7"),  # bishop trapped on the a-file
        ("8/6kB/6p1/8/8/8/8/6K1 w - - 0 1", "h7"),  # bishop trapped by the king
        ("k6n/8/8/5P2/2B5/8/8/6KR b - - 0 1", "h8"),  # knight in the corner
    ],
)
def test_trapped_piece(fen: str, trapped: str) -> None:
    fact = next(
        fact for fact in trapped_pieces(chess.Board(fen)) if fact.id == f"trapped:{trapped}"
    )
    assert fact.type is FactType.TRAPPED_PIECE
    assert fact.squares == [trapped]


@pytest.mark.parametrize(
    "fen",
    [
        "r5k1/B7/8/8/8/8/8/6K1 w - - 0 1",  # bishop attacked but can retreat along the diagonal
        "k6n/8/8/8/8/8/8/6KR b - - 0 1",  # knight attacked but has safe squares
        "r5k1/B7/8/8/8/8/8/R5K1 w - - 0 1",  # attacked bishop is defended by a rook
    ],
)
def test_not_trapped(fen: str) -> None:
    assert trapped_pieces(chess.Board(fen)) == []


THREAT_FENS = [
    "r5k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1",
    "3r2k1/5ppp/8/8/8/8/5PPP/6K1 w - - 0 1",
    "5rk1/5pp1/8/7Q/8/8/2B5/6K1 b - - 0 1",
    "6k1/3q4/2n5/5b2/8/3B4/8/2R3K1 b - - 0 1",
    "r5k1/B1p5/1p6/8/8/8/8/6K1 w - - 0 1",
]


@pytest.mark.parametrize("fen", THREAT_FENS)
def test_packet_includes_threat_facts_with_parallel_pieces(fen: str) -> None:
    packet = build_fact_packet(chess.Board(fen))
    threat_types = {
        FactType.BACK_RANK_WEAKNESS,
        FactType.MATE_THREAT,
        FactType.OVERLOADED_DEFENDER,
        FactType.TRAPPED_PIECE,
    }
    assert {fact.type for fact in packet.facts} & threat_types
    for fact in packet.facts:
        assert [piece.square for piece in fact.pieces] == fact.squares
