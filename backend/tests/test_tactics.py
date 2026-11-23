from collections.abc import Callable

import chess
import pytest

from chess_tutor.facts import (
    Fact,
    FactType,
    Side,
    build_fact_packet,
    discovered_attacks,
    fork_opportunities,
    forks,
    pins,
    skewers,
)

START = chess.STARTING_FEN
OPENING = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"
QUIET_POSITIONS = [START, OPENING]


def ids(facts: list[Fact]) -> set[str]:
    return {fact.id for fact in facts}


@pytest.mark.parametrize("fen", QUIET_POSITIONS)
@pytest.mark.parametrize("detector", [forks, fork_opportunities, pins, skewers, discovered_attacks])
def test_quiet_positions_have_no_tactics(
    fen: str, detector: Callable[[chess.Board], list[Fact]]
) -> None:
    assert detector(chess.Board(fen)) == []


@pytest.mark.parametrize("fen", QUIET_POSITIONS)
def test_packet_has_only_material_in_quiet_positions(fen: str) -> None:
    packet = build_fact_packet(chess.Board(fen))
    assert {fact.type for fact in packet.facts} == {FactType.MATERIAL_BALANCE}


@pytest.mark.parametrize(
    ("fen", "expected_id", "victims"),
    [
        # knight forks king and rook
        ("r3k3/2N5/8/8/8/8/8/4K3 b - - 0 1", "fork:c7", {"a8", "e8"}),
        # pawn forks two knights
        ("4k3/8/2n1n3/3P4/8/8/8/4K3 b - - 0 1", "fork:d5", {"c6", "e6"}),
        # king forks two undefended pieces
        ("8/8/8/2r5/3K4/4n3/8/7k w - - 0 1", "fork:d4", {"c5", "e3"}),
        # black queen forks an undefended rook and bishop
        ("4k3/8/8/8/3q4/8/1B6/3R3K b - - 0 1", "fork:d4", {"b2", "d1"}),
    ],
)
def test_existing_forks(fen: str, expected_id: str, victims: set[str]) -> None:
    board = chess.Board(fen)
    fact = next(fact for fact in forks(board) if fact.id == expected_id)
    assert fact.type is FactType.FORK
    assert set(fact.squares[1:]) == victims
    forker = board.piece_at(chess.parse_square(fact.squares[0]))
    assert forker is not None
    assert fact.side is (Side.WHITE if forker.color else Side.BLACK)


@pytest.mark.parametrize(
    "fen",
    [
        # knight attacks only one piece
        "r3k3/8/2N5/8/8/8/8/4K3 w - - 0 1",
        # knight attacks two knights that defend each other
        "4k3/8/4p3/3n1n2/8/4N3/8/4K3 w - - 0 1",
        # pawn attacks two pawns that defend each other
        "4k3/3p4/2p1p3/3P4/8/8/8/4K3 w - - 0 1",
        # forker is pinned to its king, so it cannot take either piece
        "4k3/8/8/8/1b6/1r3r2/3N4/4K3 w - - 0 1",
    ],
)
def test_not_a_fork(fen: str) -> None:
    assert forks(chess.Board(fen)) == []


@pytest.mark.parametrize(
    ("fen", "move_id", "san"),
    [
        ("r3k3/8/4N3/8/8/8/8/4K3 w - - 0 1", "fork_opportunity:e6c7", "Nc7+"),
        ("4k3/8/2n1n3/8/3P4/8/8/4K3 w - - 0 1", "fork_opportunity:d4d5", "d5"),
        ("8/1n6/8/8/6b1/8/8/1R2K2k w - - 0 1", "fork_opportunity:b1b4", "Rb4"),
    ],
)
def test_fork_opportunities(fen: str, move_id: str, san: str) -> None:
    fact = next(fact for fact in fork_opportunities(chess.Board(fen)) if fact.id == move_id)
    assert fact.san_lines == [[san]]
    assert fact.side is Side.WHITE


def test_fork_opportunity_skipped_when_forker_is_lost() -> None:
    board = chess.Board("r2qk3/8/4N3/8/8/8/8/4K3 w - - 0 1")
    assert "fork_opportunity:e6c7" not in ids(fork_opportunities(board))


def test_fork_opportunities_are_for_side_to_move_only() -> None:
    board = chess.Board("r3k3/8/4N3/8/8/8/8/4K3 b - - 0 1")
    assert all(fact.side is Side.BLACK for fact in fork_opportunities(board))


@pytest.mark.parametrize(
    ("fen", "pin_id", "absolute", "behind"),
    [
        # bishop pins a knight to the king
        ("4k3/8/8/8/1b6/8/3N4/4K3 w - - 0 1", "pin:b4:d2", True, "e1"),
        # rook pins a pawn to the king
        ("4k3/4p3/8/8/8/8/8/4R1K1 b - - 0 1", "pin:e1:e7", True, "e8"),
        # rook pins a knight to the queen
        ("4r2k/8/8/8/4N3/8/4Q3/6K1 w - - 0 1", "pin:e8:e4", False, "e2"),
        # bishop pins a knight to a rook
        ("k7/7r/8/8/8/3n4/8/1B2K3 w - - 0 1", "pin:b1:d3", False, "h7"),
    ],
)
def test_pins(fen: str, pin_id: str, absolute: bool, behind: str) -> None:
    board = chess.Board(fen)
    fact = next(fact for fact in pins(board) if fact.id == pin_id)
    assert fact.type is FactType.PIN
    assert fact.squares[2] == behind
    assert ("absolutely" in fact.summary) is absolute


def test_absolute_pins_agree_with_python_chess() -> None:
    board = chess.Board("4k3/4p3/8/8/1b6/8/3N4/4R1K1 w - - 0 1")
    pinned = {fact.squares[1] for fact in pins(board) if "absolutely" in fact.summary}
    expected = {
        chess.square_name(square)
        for color in (chess.WHITE, chess.BLACK)
        for square in chess.SquareSet(board.occupied_co[color])
        if board.is_pinned(color, square)
    }
    assert pinned == expected


@pytest.mark.parametrize(
    "fen",
    [
        # lesser piece behind: a skewer, not a pin
        "7k/4p3/8/8/4q3/8/8/4R1K1 w - - 0 1",
        # attacker is blocked by its own piece
        "4k3/8/8/4n3/4p3/4P3/4R3/6K1 w - - 0 1",
        # two enemy pieces side by side, not on the slider's line
        "4k3/8/8/8/8/2n1n3/8/R3K3 w - - 0 1",
    ],
)
def test_not_a_pin(fen: str) -> None:
    assert pins(chess.Board(fen)) == []


@pytest.mark.parametrize(
    ("fen", "skewer_id", "behind"),
    [
        # rook skewers king, then rook
        ("k7/8/8/8/8/8/8/3RK2r w - - 0 1", "skewer:h1:e1", "d1"),
        # bishop skewers queen, then rook
        ("k7/8/8/4R3/8/2Q5/8/b3K3 w - - 0 1", "skewer:a1:c3", "e5"),
        # equal values, the one behind is undefended
        ("r6k/8/8/8/N7/8/8/B3K3 w - - 0 1", "skewer:a8:a4", "a1"),
    ],
)
def test_skewers(fen: str, skewer_id: str, behind: str) -> None:
    fact = next(fact for fact in skewers(chess.Board(fen)) if fact.id == skewer_id)
    assert fact.type is FactType.SKEWER
    assert fact.squares[2] == behind


@pytest.mark.parametrize(
    "fen",
    [
        # equal values but the king defends the bishop behind the knight
        "r6k/8/8/8/N7/8/1K6/B7 w - - 0 1",
        # more valuable piece behind: a pin, not a skewer
        "4r2k/8/8/8/8/8/4N3/4Q1K1 b - - 0 1",
        # the pieces are the slider's own colour
        "4k3/8/8/8/8/8/4R3/4R1K1 w - - 0 1",
    ],
)
def test_not_a_skewer(fen: str) -> None:
    assert skewers(chess.Board(fen)) == []


@pytest.mark.parametrize(
    ("fen", "discovery_id", "san"),
    [
        # knight uncovers a check from the rook
        ("4k3/8/8/8/4N3/8/8/4R1K1 w - - 0 1", "discovered:e4c5:e1:e8", "Nc5+"),
        # bishop uncovers a rook attack on the queen
        ("3q2k1/8/8/8/3B4/8/8/3R2K1 w - - 0 1", "discovered:d4e5:d1:d8", "Be5"),
        # pawn uncovers a bishop attack on the rook
        ("6kr/8/8/8/3P4/8/8/B3K3 w - - 0 1", "discovered:d4d5:a1:h8", "d5"),
    ],
)
def test_discovered_attacks(fen: str, discovery_id: str, san: str) -> None:
    fact = next(fact for fact in discovered_attacks(chess.Board(fen)) if fact.id == discovery_id)
    assert fact.type is FactType.DISCOVERED_ATTACK
    assert fact.san_lines == [[san]]
    assert fact.side is Side.WHITE


def test_discovery_ignores_undefended_pawns() -> None:
    assert discovered_attacks(chess.Board("k7/6p1/8/8/3P4/8/8/B3K3 w - - 0 1")) == []


def test_no_discovery_when_nothing_is_unmasked() -> None:
    # the knight is not between the rook and the king
    assert discovered_attacks(chess.Board("4k3/8/8/8/N7/8/8/4R1K1 w - - 0 1")) == []


def test_packet_includes_tactics() -> None:
    packet = build_fact_packet(chess.Board("r3k3/8/4N3/8/8/8/8/4K3 w - - 0 1"))
    assert packet.fact("fork_opportunity:e6c7") is not None


def test_fork_dropped_when_forker_can_be_captured() -> None:
    # the knight forks king and queen but the d6 pawn takes it
    board = chess.Board("8/3q4/3p2k1/4N3/8/8/8/4K3 b - - 0 1")
    assert forks(board) == []
    assert "fork:e5" in ids(forks(chess.Board("8/3q4/6k1/4N3/8/8/8/4K3 b - - 0 1")))


INVARIANT_FENS = [
    START,
    OPENING,
    "r3k3/2N5/8/8/8/8/8/4K3 b - - 0 1",
    "r3k3/8/4N3/8/8/8/8/4K3 w - - 0 1",
    "4k3/8/2n1n3/3P4/8/8/8/4K3 b - - 0 1",
    "4k3/8/8/8/1b6/8/3N4/4K3 w - - 0 1",
    "4r2k/8/8/8/4N3/8/4Q3/6K1 w - - 0 1",
    "k7/8/8/8/8/8/8/3RK2r w - - 0 1",
    "k7/8/8/4R3/8/2Q5/8/b3K3 w - - 0 1",
    "4k3/8/8/8/4N3/8/8/4R1K1 w - - 0 1",
    "3q2k1/8/8/8/3B4/8/8/3R2K1 w - - 0 1",
    "6kr/8/8/8/3P4/8/8/B3K3 w - - 0 1",
]


@pytest.mark.parametrize("fen", INVARIANT_FENS)
def test_squares_and_pieces_are_parallel(fen: str) -> None:
    for fact in build_fact_packet(chess.Board(fen)).facts:
        assert [piece.square for piece in fact.pieces] == fact.squares
