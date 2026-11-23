import chess
import pytest

from chess_tutor.facts import FactType, Side, build_fact_packet, hanging_pieces, material_balance

START = chess.STARTING_FEN


def hanging_ids(fen: str) -> set[str]:
    return {fact.id for fact in hanging_pieces(chess.Board(fen))}


@pytest.mark.parametrize(
    ("fen", "expected"),
    [
        # undefended knight attacked by a rook
        ("4k3/8/8/4n3/8/8/8/4RK2 w - - 0 1", {"hanging:e5"}),
        # knight defended by a pawn but attacked by a pawn
        ("4k3/8/3p4/4n3/3P4/8/8/4K3 w - - 0 1", {"hanging:e5"}),
        # queen attacked by a defended pawn
        ("4k3/8/8/3q4/4P3/5K2/8/8 b - - 0 1", {"hanging:d5"}),
        # both colours reported: two undefended rooks attack each other
        ("4k3/8/8/8/r6R/8/8/4K3 w - - 0 1", {"hanging:a4", "hanging:h4"}),
        # the king cannot recapture while a second attacker covers the square
        ("3rk3/8/8/6b1/8/8/3N4/4K3 w - - 0 1", {"hanging:d2"}),
        # the only defender is pinned along a diagonal that does not include the target
        ("5k2/8/3b4/4n3/1B6/8/8/4R1K1 w - - 0 1", {"hanging:b4", "hanging:d6", "hanging:e5"}),
        # attacked twice by knights, defended once by a knight: the exchange wins a piece
        ("4k3/8/2n5/4n3/2N5/5N2/8/4K3 w - - 0 1", {"hanging:c4", "hanging:e5", "hanging:f3"}),
        # doubled rooks: Rxd5 exd5 Rxd5 wins, the second rook joins through the first (x-ray)
        ("4k3/8/4p3/3r4/8/8/3R4/3RK3 w - - 0 1", {"hanging:d5"}),
        # Rxd8+ wins: the king can't recapture because the queen behind the rook covers d8.
        # White's d2 rook (attacked once, defended by the queen) is not hanging.
        ("3rk3/8/8/8/8/8/3R4/3QK3 w - - 0 1", {"hanging:d8"}),
        # Kxd2 is illegal: the pinned bishop can't capture but still guards d2 against the king
        ("2k5/8/8/8/8/2b5/3n4/2R1K3 w - - 0 1", {"hanging:c3"}),
    ],
)
def test_hanging_positive(fen: str, expected: set[str]) -> None:
    assert hanging_ids(fen) == expected


@pytest.mark.parametrize(
    "fen",
    [
        START,
        # rooks attack each other once and each is defended once by its king
        "8/8/8/4k3/3r4/8/8/3RK3 w - - 0 1",
        # a defender that is not pinned still defends
        "5k2/8/3b4/4n3/8/8/8/4R1K1 w - - 0 1",
        # attacked twice by rooks, defended by a pawn: Rxe5 dxe5 loses the exchange
        "4k3/8/3p4/R3n3/8/8/8/4R2K w - - 0 1",
    ],
)
def test_hanging_negative(fen: str) -> None:
    assert hanging_ids(fen) == set()


def test_kings_are_never_hanging() -> None:
    assert hanging_ids("4k3/8/8/8/8/8/8/3rK3 w - - 0 1") == {"hanging:d1"}


def test_material_balance_known_imbalance() -> None:
    board = chess.Board("4k3/8/8/8/8/8/PP6/1N2K3 w - - 0 1")
    fact = material_balance(board)
    assert fact.type is FactType.MATERIAL_BALANCE
    assert fact.severity == 5
    assert fact.side is Side.WHITE
    assert material_balance(chess.Board(START)).summary == "Material is equal"
    black = material_balance(chess.Board("4k3/8/8/8/8/8/8/1n2K3 w - - 0 1"))
    assert black.summary == "Black is up a knight or bishop"
    assert black.side is Side.BLACK


def test_packet_is_deterministic_and_lookup_works() -> None:
    fen = "4k3/8/3p4/4n3/3P4/8/8/4K3 w - - 0 1"
    first = build_fact_packet(chess.Board(fen))
    assert first == build_fact_packet(chess.Board(fen))
    assert first.to_compact_json() == build_fact_packet(chess.Board(fen)).to_compact_json()
    assert first.side_to_move is Side.WHITE
    found = first.fact("hanging:e5")
    assert found is not None
    assert found.pieces[0].piece_type == "knight"
    assert found.pieces[0].color is Side.BLACK
    assert first.fact("hanging:zz") is None


def test_start_packet_is_compact() -> None:
    payload = build_fact_packet(chess.Board()).to_compact_json()
    assert len(payload) < 250
    assert "san_lines" not in payload
