from dataclasses import dataclass

import chess

from chess_tutor.facts.detectors import (
    capturers,
    exchange_gain,
    piece_ref,
    piece_value,
)
from chess_tutor.facts.models import Fact, FactType, PieceRef

DIAGONALS = ((1, 1), (1, -1), (-1, 1), (-1, -1))
ORTHOGONALS = ((1, 0), (-1, 0), (0, 1), (0, -1))
SLIDER_DIRECTIONS = {
    chess.BISHOP: DIAGONALS,
    chess.ROOK: ORTHOGONALS,
    chess.QUEEN: DIAGONALS + ORTHOGONALS,
}


def _names(board: chess.Board, squares: list[chess.Square]) -> list[PieceRef]:
    return [piece_ref(board, square) for square in squares]


def _label(ref: PieceRef) -> str:
    return f"{ref.color.value} {ref.piece_type} on {ref.square}"


def _is_worth_attacking(board: chess.Board, target: chess.Square, attacker_value: int) -> bool:
    """A target is worth attacking if it is the king, undefended, or more valuable than the
    attacker."""
    if board.piece_type_at(target) == chess.KING:
        return True
    owner = board.color_at(target)
    assert owner is not None
    return not board.is_attacked_by(owner, target) or piece_value(board, target) > attacker_value


def _fork_targets(board: chess.Board, forker: chess.Square) -> list[chess.Square]:
    color = board.color_at(forker)
    assert color is not None
    attacked = board.attacks(forker) & board.occupied_co[not color]
    value = piece_value(board, forker)
    return [
        target
        for target in sorted(attacked)
        if forker in capturers(board, color, target) and _is_worth_attacking(board, target, value)
    ]


def _fork_severity(board: chess.Board, targets: list[chess.Square]) -> int:
    """What the forker wins: the second most valuable target, since the best one can be saved."""
    return sorted((piece_value(board, target) for target in targets), reverse=True)[1]


def _fork_fact(
    board: chess.Board,
    fact_id: str,
    forker: chess.Square,
    targets: list[chess.Square],
    san_lines: list[list[str]],
) -> Fact:
    refs = _names(board, [forker, *targets])
    victims = ", ".join(f"{ref.piece_type} on {ref.square}" for ref in refs[1:])
    return Fact(
        id=fact_id,
        type=FactType.FORK,
        side=refs[0].color,
        summary=f"{_label(refs[0])} forks {victims}",
        severity=_fork_severity(board, targets),
        squares=[ref.square for ref in refs],
        pieces=refs,
        san_lines=san_lines,
    )


def forks(board: chess.Board) -> list[Fact]:
    facts = []
    for forker in sorted(board.piece_map()):
        targets = _fork_targets(board, forker)
        if len(targets) >= 2:
            facts.append(
                _fork_fact(board, f"fork:{chess.square_name(forker)}", forker, targets, [])
            )
    return facts


def fork_opportunities(board: chess.Board) -> list[Fact]:
    """Legal moves for the side to move that create a fork the forker survives."""
    facts = []
    scratch = board.copy(stack=False)
    for move in sorted(board.legal_moves, key=lambda candidate: candidate.uci()):
        san = board.san(move)
        scratch.push(move)
        targets = _fork_targets(scratch, move.to_square)
        survives = exchange_gain(scratch, move.to_square, scratch.turn) == 0
        if len(targets) >= 2 and survives:
            facts.append(
                _fork_fact(
                    scratch, f"fork_opportunity:{move.uci()}", move.to_square, targets, [[san]]
                )
            )
        scratch.pop()
    return facts


@dataclass(frozen=True)
class RayMotif:
    kind: FactType
    slider: chess.Square
    front: chess.Square
    behind: chess.Square
    absolute: bool = False


def _pieces_along(
    board: chess.Board, start: chess.Square, file_step: int, rank_step: int
) -> list[chess.Square]:
    """The first two occupied squares met walking away from `start`."""
    found: list[chess.Square] = []
    file = chess.square_file(start) + file_step
    rank = chess.square_rank(start) + rank_step
    while 0 <= file < 8 and 0 <= rank < 8 and len(found) < 2:
        square = chess.square(file, rank)
        if board.piece_at(square):
            found.append(square)
        file += file_step
        rank += rank_step
    return found


def _classify(
    board: chess.Board, slider: chess.Square, front: chess.Square, behind: chess.Square
) -> RayMotif | None:
    if board.piece_type_at(behind) == chess.KING:
        return RayMotif(FactType.PIN, slider, front, behind, absolute=True)
    front_value = piece_value(board, front)
    behind_value = piece_value(board, behind)
    if behind_value > front_value:
        return RayMotif(FactType.PIN, slider, front, behind)
    undefended_equal = behind_value == front_value and _is_worth_attacking(
        board, behind, front_value
    )
    if front_value > behind_value or undefended_equal:
        return RayMotif(FactType.SKEWER, slider, front, behind)
    return None


def _ray_motifs(board: chess.Board) -> list[RayMotif]:
    motifs = []
    for slider, piece in sorted(board.piece_map().items()):
        for file_step, rank_step in SLIDER_DIRECTIONS.get(piece.piece_type, ()):
            found = _pieces_along(board, slider, file_step, rank_step)
            if len(found) < 2:
                continue
            front, behind = found
            enemy = board.color_at(front) != piece.color
            if enemy and board.color_at(behind) == board.color_at(front):
                motif = _classify(board, slider, front, behind)
                if motif:
                    motifs.append(motif)
    return motifs


def _ray_fact(board: chess.Board, motif: RayMotif) -> Fact:
    refs = _names(board, [motif.slider, motif.front, motif.behind])
    pinner, front, behind = refs
    if motif.kind == FactType.PIN:
        kind = "absolutely pinned to the king" if motif.absolute else "pinned to"
        summary = f"{_label(pinner)} pins {front.piece_type} on {front.square}"
        summary += f" ({kind} {behind.piece_type} on {behind.square})"
        value = piece_value(board, motif.front)
    else:
        summary = f"{_label(pinner)} skewers {front.piece_type} on {front.square}"
        summary += f" with {behind.piece_type} on {behind.square} behind it"
        value = piece_value(board, motif.behind)
    return Fact(
        id=f"{motif.kind.value}:{pinner.square}:{front.square}",
        type=motif.kind,
        side=pinner.color,
        summary=summary,
        severity=value,
        squares=[ref.square for ref in refs],
        pieces=refs,
    )


def _ray_facts(board: chess.Board, kind: FactType) -> list[Fact]:
    return [_ray_fact(board, motif) for motif in _ray_motifs(board) if motif.kind == kind]


def pins(board: chess.Board) -> list[Fact]:
    return _ray_facts(board, FactType.PIN)


def skewers(board: chess.Board) -> list[Fact]:
    return _ray_facts(board, FactType.SKEWER)


def _unmasked_targets(
    before: chess.Board, after: chess.Board, slider: chess.Square
) -> list[chess.Square]:
    """Valuable enemy pieces the slider attacks after the move but did not before."""
    color = after.color_at(slider)
    assert color is not None
    value = piece_value(after, slider)
    newly = (after.attacks(slider) & after.occupied_co[not color]) & ~before.attacks(slider)
    return [
        target
        for target in sorted(newly)
        if after.piece_type_at(target) != chess.PAWN and _is_worth_attacking(after, target, value)
    ]


def _friendly_sliders(
    board: chess.Board, color: chess.Color, moved_to: chess.Square
) -> list[chess.Square]:
    return [
        square
        for square, piece in sorted(board.piece_map().items())
        if piece.color == color and piece.piece_type in SLIDER_DIRECTIONS and square != moved_to
    ]


def _discovered_fact(
    after: chess.Board, move: chess.Move, san: str, slider: chess.Square, target: chess.Square
) -> Fact:
    refs = _names(after, [move.to_square, slider, target])
    mover, revealed, victim = refs
    kind = "check" if victim.piece_type == "king" else "attack"
    return Fact(
        id=f"discovered:{move.uci()}:{revealed.square}:{victim.square}",
        type=FactType.DISCOVERED_ATTACK,
        side=mover.color,
        summary=(
            f"Moving the {mover.piece_type} ({san}) uncovers a {kind} from "
            f"{revealed.piece_type} on {revealed.square} on {victim.piece_type} on {victim.square}"
        ),
        severity=piece_value(after, target),
        squares=[chess.square_name(move.from_square), revealed.square, victim.square],
        pieces=refs,
        san_lines=[[san]],
    )


def discovered_attacks(board: chess.Board) -> list[Fact]:
    """Moves by the side to move that unmask a friendly slider on a valuable enemy piece."""
    facts = []
    scratch = board.copy(stack=False)
    for move in sorted(board.legal_moves, key=lambda candidate: candidate.uci()):
        san = board.san(move)
        scratch.push(move)
        for slider in _friendly_sliders(scratch, board.turn, move.to_square):
            for target in _unmasked_targets(board, scratch, slider):
                facts.append(_discovered_fact(scratch, move, san, slider, target))
        scratch.pop()
    return facts
