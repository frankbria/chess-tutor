"""Mate and tempo-based detectors: back-rank weakness, mate threats, overloaded defenders,
trapped pieces.

Cost: mate detection pushes every legal move on a board copy, like the motif detectors.
"""

import chess

from chess_tutor.facts.detectors import (
    exchange_gain,
    piece_ref,
    piece_value,
    side_of,
)
from chess_tutor.facts.models import Fact, FactType, PieceRef

MATE_SEVERITY = 100
BACK_RANK_SEVERITY = 5
HEAVY_PIECES = (chess.ROOK, chess.QUEEN)


def _label(ref: PieceRef) -> str:
    return f"{ref.color.value} {ref.piece_type} on {ref.square}"


def _back_rank(color: chess.Color) -> int:
    return 0 if color == chess.WHITE else 7


def _heavy_pieces(board: chess.Board, color: chess.Color) -> chess.SquareSet:
    return board.pieces(chess.ROOK, color) | board.pieces(chess.QUEEN, color)


def _king_has_luft(board: chess.Board, king: chess.Square, color: chess.Color) -> bool:
    """True if a square next to the king, off the back rank, is free of own pieces and not
    attacked by the enemy."""
    off_rank = [
        square
        for square in chess.SquareSet(chess.BB_KING_ATTACKS[king])
        if chess.square_rank(square) != _back_rank(color)
    ]
    return any(
        board.color_at(square) != color and not board.is_attacked_by(not color, square)
        for square in off_rank
    )


def _is_back_rank_weak(board: chess.Board, color: chess.Color) -> bool:
    king = board.king(color)
    if king is None or chess.square_rank(king) != _back_rank(color):
        return False
    if not _heavy_pieces(board, not color) or _king_has_luft(board, king, color):
        return False
    guards = [
        square
        for square in _heavy_pieces(board, color)
        if chess.square_rank(square) == _back_rank(color)
    ]
    # shortcut: counts own rooks/queens on the back rank instead of testing which of them
    # actually cover the mating square, upgrade when the coach needs the exact defender.
    return len(guards) <= 1


def back_rank_weaknesses(board: chess.Board) -> list[Fact]:
    facts = []
    for color in (chess.WHITE, chess.BLACK):
        if not _is_back_rank_weak(board, color):
            continue
        king = piece_ref(board, board.king(color) or chess.A1)
        facts.append(
            Fact(
                id=f"back_rank:{king.square}",
                type=FactType.BACK_RANK_WEAKNESS,
                side=king.color,
                summary=(
                    f"{_label(king)} has no escape square off the back rank and little defending it"
                ),
                severity=BACK_RANK_SEVERITY,
                squares=[king.square],
                pieces=[king],
            )
        )
    return facts


def _mating_moves(board: chess.Board) -> list[chess.Move]:
    probe = board.copy(stack=False)
    mates = []
    for move in list(probe.legal_moves):
        probe.push(move)
        if probe.is_checkmate():
            mates.append(move)
        probe.pop()
    return mates


def _mate_facts(board: chess.Board, fact_type: FactType, id_prefix: str) -> list[Fact]:
    mover = board.turn
    facts = []
    for move in _mating_moves(board):
        piece = piece_ref(board, move.from_square)
        facts.append(
            Fact(
                id=f"{id_prefix}:{move.uci()}",
                type=fact_type,
                side=side_of(mover),
                summary=f"{_label(piece)} can give checkmate with {board.san(move)}",
                severity=MATE_SEVERITY,
                squares=[piece.square],
                pieces=[piece],
                san_lines=[[board.san(move)]],
            )
        )
    return facts


def mates_in_one(board: chess.Board) -> list[Fact]:
    return _mate_facts(board, FactType.MATE_IN_ONE, "mate_in_one")


def mate_threats(board: chess.Board) -> list[Fact]:
    """Mates the side not to move would have if it were its turn (null-move probe)."""
    if board.is_check():
        return []
    probe = board.copy(stack=False)
    probe.push(chess.Move.null())
    return _mate_facts(probe, FactType.MATE_THREAT, "mate_threat")


def _critical_victims(board: chess.Board, defender: chess.Square) -> list[chess.Square]:
    """Attacked friendly pieces that are safe only because `defender` guards them."""
    color = board.color_at(defender)
    assert color is not None
    without_defender = board.copy(stack=False)
    without_defender.remove_piece_at(defender)
    return [
        square
        for square in sorted(board.attacks(defender) & board.occupied_co[color])
        if board.piece_type_at(square) != chess.KING
        and exchange_gain(board, square, not color) == 0
        and exchange_gain(without_defender, square, not color) > 0
    ]


def overloaded_defenders(board: chess.Board) -> list[Fact]:
    # shortcut: only guarded pieces count, not guarded mate squares, add them when the coach
    # needs back-rank overloads.
    facts = []
    for square, piece in sorted(board.piece_map().items()):
        victims = _critical_victims(board, square) if piece.piece_type != chess.KING else []
        if len(victims) < 2:
            continue
        refs = [piece_ref(board, target) for target in [square, *victims]]
        named = ", ".join(f"{ref.piece_type} on {ref.square}" for ref in refs[1:])
        facts.append(
            Fact(
                id=f"overloaded:{refs[0].square}",
                type=FactType.OVERLOADED_DEFENDER,
                side=refs[0].color,
                summary=f"{_label(refs[0])} is the only defender of {named}",
                severity=min(piece_value(board, target) for target in victims),
                squares=[ref.square for ref in refs],
                pieces=refs,
            )
        )
    return facts


def _is_lost_after(board: chess.Board, move: chess.Move) -> bool:
    mover = board.color_at(move.from_square)
    assert mover is not None
    after = board.copy(stack=False)
    after.push(move)
    return exchange_gain(after, move.to_square, not mover) > 0


def _is_trapped(board: chess.Board, square: chess.Square) -> bool:
    color = board.color_at(square)
    assert color is not None
    if exchange_gain(board, square, not color) == 0:
        return False
    owner_to_move = board.copy(stack=False)
    owner_to_move.turn = color
    owner_to_move.ep_square = None
    moves = [move for move in owner_to_move.legal_moves if move.from_square == square]
    return all(_is_lost_after(owner_to_move, move) for move in moves)


def trapped_pieces(board: chess.Board) -> list[Fact]:
    """Attacked pieces with no safe square. Pawns are excluded: they can only move forward,
    so "no safe move" is the normal state of a blocked pawn and says nothing useful. The king
    is excluded because its safety is covered by check and mate facts."""
    # shortcut: only pieces already attacked, not ones a pawn push would trap next move.
    facts = []
    for square, piece in sorted(board.piece_map().items()):
        if piece.piece_type in (chess.PAWN, chess.KING) or not _is_trapped(board, square):
            continue
        ref = piece_ref(board, square)
        facts.append(
            Fact(
                id=f"trapped:{ref.square}",
                type=FactType.TRAPPED_PIECE,
                side=ref.color,
                summary=f"{_label(ref)} is attacked and has no safe square",
                severity=piece_value(board, square),
                squares=[ref.square],
                pieces=[ref],
            )
        )
    return facts
