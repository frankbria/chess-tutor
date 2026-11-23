import chess

from chess_tutor.facts.models import Fact, FactPacket, FactType, PieceRef, Side

PIECE_VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
}
# A king can only capture an undefended piece, so its value never decides a trade.
KING_AS_ATTACKER_VALUE = 100


def _side(color: chess.Color) -> Side:
    return Side.WHITE if color == chess.WHITE else Side.BLACK


def _piece_ref(board: chess.Board, square: chess.Square) -> PieceRef:
    piece = board.piece_at(square)
    assert piece is not None
    return PieceRef(
        color=_side(piece.color),
        piece_type=chess.piece_name(piece.piece_type),
        square=chess.square_name(square),
    )


def _material(board: chess.Board, color: chess.Color) -> int:
    return sum(
        value * len(board.pieces(piece_type, color)) for piece_type, value in PIECE_VALUES.items()
    )


def _describe_lead(difference: int) -> str:
    leader = "White" if difference > 0 else "Black"
    points = abs(difference)
    names = {1: "a pawn", 3: "a knight or bishop", 5: "a rook", 9: "a queen"}
    return f"{leader} is up {names.get(points, f'{points} points of material')}"


def material_balance(board: chess.Board) -> Fact:
    difference = _material(board, chess.WHITE) - _material(board, chess.BLACK)
    summary = "Material is equal" if difference == 0 else _describe_lead(difference)
    return Fact(
        id="material:balance",
        type=FactType.MATERIAL_BALANCE,
        side=Side.BLACK if difference < 0 else Side.WHITE,
        summary=summary,
        severity=abs(difference),
    )


def _capturers(board: chess.Board, color: chess.Color, target: chess.Square) -> list[chess.Square]:
    """Pieces of `color` that attack `target` and are not pinned away from it."""
    return [
        square
        for square in board.attackers(color, target)
        if not board.is_pinned(color, square) or target in chess.SquareSet(board.pin(color, square))
    ]


def _value(board: chess.Board, square: chess.Square) -> int:
    piece_type = board.piece_type_at(square)
    assert piece_type is not None
    return PIECE_VALUES.get(piece_type, KING_AS_ATTACKER_VALUE)


KING_EXCHANGE_VALUE = 100


def _is_hanging(board: chess.Board, square: chess.Square, color: chess.Color) -> bool:
    attackers = _capturers(board, not color, square)
    if not attackers:
        return False
    defenders = _capturers(board, color, square)
    return (
        _exchange_gain(
            _value(board, square),
            sorted(_exchange_value(board, a) for a in attackers),
            sorted(_exchange_value(board, d) for d in defenders),
        )
        > 0
    )


def _exchange_value(board: chess.Board, square: chess.Square) -> int:
    # A king may only capture last: giving it a huge value makes recapturing it never pay.
    return (
        KING_EXCHANGE_VALUE if board.piece_type_at(square) == chess.KING else _value(board, square)
    )


def _exchange_gain(target_value: int, attackers: list[int], defenders: list[int]) -> int:
    """Static exchange: what the side to capture wins on this square, each side capturing with
    its cheapest piece and free to stop. Values must be sorted ascending."""
    # shortcut: ignores x-ray attackers behind the first capturer, add them if batteries matter
    if not attackers:
        return 0
    return max(0, target_value - _exchange_gain(attackers[0], defenders, attackers[1:]))


def hanging_pieces(board: chess.Board) -> list[Fact]:
    facts = []
    for color in (chess.WHITE, chess.BLACK):
        for square, piece in sorted(board.piece_map().items()):
            if piece.color != color or piece.piece_type == chess.KING:
                continue
            if not _is_hanging(board, square, color):
                continue
            ref = _piece_ref(board, square)
            facts.append(
                Fact(
                    id=f"hanging:{ref.square}",
                    type=FactType.HANGING_PIECE,
                    side=ref.color,
                    summary=f"{ref.color.value} {ref.piece_type} on {ref.square} is hanging",
                    severity=PIECE_VALUES[piece.piece_type],
                    squares=[ref.square],
                    pieces=[ref],
                )
            )
    return facts


def build_fact_packet(board: chess.Board) -> FactPacket:
    return FactPacket(
        fen=board.fen(),
        side_to_move=_side(board.turn),
        facts=[material_balance(board), *hanging_pieces(board)],
    )
