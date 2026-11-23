"""The "unseen" detector: what the opponent threatens now, and what a move changes.

`best_move` and `best_line` stay out of the facts on purpose, so a caller can withhold them
from the coach (Active mode, before lock-in) without touching anything else.
"""

import asyncio
from dataclasses import dataclass, field

import chess

from chess_tutor.analysis.classification import (
    analyse_position,
    win_probability_for,
)
from chess_tutor.engines.stockfish import AnalysedLine, Budget, StockfishPool
from chess_tutor.facts.detectors import piece_ref, side_of
from chess_tutor.facts.models import Fact, FactType, PieceRef, Side
from chess_tutor.facts.packet import build_fact_packet

# Win-probability points an opponent reply must gain (or a move lose) to count as a threat.
THREAT_MIN_SWING = 10.0
REPLY_LINES = 3
# Facts are tagged with the side that owns the attacker or the victim, by type. Positional and
# material facts say nothing about what a single reply gained, so they are left out.
ATTACKER_SIDED = {
    FactType.FORK,
    FactType.PIN,
    FactType.SKEWER,
    FactType.DISCOVERED_ATTACK,
    FactType.MATE_IN_ONE,
    FactType.MATE_THREAT,
}
VICTIM_SIDED = {
    FactType.HANGING_PIECE,
    FactType.BACK_RANK_WEAKNESS,
    FactType.OVERLOADED_DEFENDER,
    FactType.TRAPPED_PIECE,
}
SHOWN_REPLY_PLIES = 3


@dataclass(frozen=True)
class ThreatReport:
    """What the side not to move would do with an extra turn; `move` is None when nothing."""

    threatening_side: Side
    win_probability_before: float  # the threatening side's chance in the position as it is
    win_probability_after: float  # ... if it were to move now
    move: chess.Move | None = None
    san: str | None = None
    facts: list[Fact] = field(default_factory=list)


@dataclass(frozen=True)
class MoveDelta:
    move_san: str
    win_probability_before: float  # the mover's chance, in percent
    win_probability_after: float
    best_move: chess.Move | None
    best_move_san: str | None
    best_line: list[str]  # SAN, starting with the best move
    reply: chess.Move | None  # the opponent's best answer to the move
    reply_san: str | None
    allowed_facts: list[Fact]  # new facts, in the position after the reply, favouring the opponent
    facts: list[Fact]  # ALLOWS and IGNORED_THREAT, citable like any other fact


def _sans(board: chess.Board, moves: list[chess.Move]) -> list[str]:
    board = board.copy(stack=False)
    sans = []
    for move in moves:
        sans.append(board.san(move))
        board.push(move)
    return sans


def _move_refs(board: chess.Board, move: chess.Move) -> list[PieceRef]:
    """The moving piece, then the piece it captures (en passant victims are skipped: the
    victim is not on the destination square)."""
    refs = [piece_ref(board, move.from_square)]
    if board.is_capture(move) and not board.is_en_passant(move):
        refs.append(piece_ref(board, move.to_square))
    return refs


def _new_facts_after(
    reference: chess.Board, board: chess.Board, move: chess.Move, side: Side
) -> list[Fact]:
    """Facts favouring `side` after `move` that the `reference` position did not have."""
    known = {fact.id for fact in build_fact_packet(reference).facts}
    after = board.copy(stack=False)
    after.push(move)
    return [
        fact
        for fact in build_fact_packet(after).facts
        if fact.id not in known
        and (
            (fact.type in ATTACKER_SIDED and fact.side is side)
            or (fact.type in VICTIM_SIDED and fact.side is not side)
        )
    ]


async def threat_probe(
    board: chess.Board, pool: StockfishPool, budget: Budget = Budget.QUICK
) -> ThreatReport:
    """Null-move probe: give the side not to move a free turn and see whether its best move
    wins material or mates. In check the threat is the check itself, so nothing is reported."""
    opponent = not board.turn
    quiet = ThreatReport(side_of(opponent), 0.0, 0.0)
    if board.is_check() or board.is_game_over():
        return quiet
    with_null_move = board.copy(stack=False)
    with_null_move.push(chess.Move.null())
    # A board whose history holds a null move can't be replayed to the engine, which then
    # answers for a stale position; start a fresh history from the same placement.
    skipped = with_null_move.copy(stack=False)
    current_lines, probed_lines = await asyncio.gather(
        analyse_position(pool, board, budget), analyse_position(pool, skipped, budget)
    )
    current, probed = current_lines[0], probed_lines[0]
    before = win_probability_for(current, opponent)
    after = win_probability_for(probed, opponent)
    report = ThreatReport(side_of(opponent), before, after)
    if not probed.pv or after - before < THREAT_MIN_SWING:
        return report
    move = probed.pv[0]
    return ThreatReport(
        report.threatening_side,
        before,
        after,
        move,
        skipped.san(move),
        _new_facts_after(board, skipped, move, report.threatening_side),
    )


def _ignored_threat_fact(
    board: chess.Board, report: ThreatReport, threat: chess.Move, swing: float
) -> Fact:
    refs = _move_refs(board, threat)
    assert report.san is not None
    return Fact(
        id=f"ignored_threat:{threat.uci()}",
        type=FactType.IGNORED_THREAT,
        side=report.threatening_side,
        summary=(
            f"{report.threatening_side.value} threatens {report.san}, and the move "
            "did not deal with it"
        ),
        severity=round(swing),
        squares=[ref.square for ref in refs],
        pieces=refs,
        san_lines=[[report.san]],
    )


def _allows_fact(
    board_after: chess.Board, reply_line: AnalysedLine, loss: float, allowed_facts: list[Fact]
) -> Fact:
    reply = reply_line.pv[0]
    refs = _move_refs(board_after, reply)
    line = _sans(board_after, reply_line.pv[:SHOWN_REPLY_PLIES])
    created = "; ".join(fact.summary for fact in allowed_facts)
    return Fact(
        id=f"allows:{reply.uci()}",
        type=FactType.ALLOWS,
        side=side_of(board_after.turn),
        summary=(f"the move allows {line[0]}" + (f", which creates: {created}" if created else "")),
        severity=round(loss),
        squares=[ref.square for ref in refs],
        pieces=refs,
        san_lines=[line],
    )


async def _threat_swing(
    board_after: chess.Board,
    threat: chess.Move,
    replies: list[AnalysedLine],
    pool: StockfishPool,
) -> float:
    """What playing `threat` gains the opponent over its best other reply in the position
    after our move, in win-probability points. A threat outside the analysed replies is
    evaluated directly."""
    opponent = board_after.turn
    threat_line = next((line for line in replies if line.pv[:1] == [threat]), None)
    if threat_line is None:
        played = board_after.copy(stack=False)
        played.push(threat)
        threat_line = (await analyse_position(pool, played, Budget.QUICK))[0]
    win_with_threat = win_probability_for(threat_line, opponent)
    alternatives = [
        win_probability_for(line, opponent) for line in replies if line.pv[:1] != [threat]
    ]
    return win_with_threat - max(alternatives, default=win_with_threat)


async def move_delta(board: chess.Board, move: chess.Move, pool: StockfishPool) -> MoveDelta:
    """What `move` (played or proposed) does: the evaluation change, the engine's best
    alternative, what the opponent's best reply creates, and any threat the move left standing."""
    mover = board.turn
    move_san = board.san(move)
    board_after = board.copy(stack=False)
    board_after.push(move)
    threats, before, after = await asyncio.gather(
        threat_probe(board, pool),
        analyse_position(pool, board, Budget.STANDARD, multipv=2),
        analyse_position(pool, board_after, Budget.STANDARD, multipv=REPLY_LINES),
    )

    win_before = win_probability_for(before[0], mover)
    win_after = win_probability_for(after[0], mover)
    best_line = before[0].pv
    reply_line = after[0] if after[0].pv else None
    facts: list[Fact] = []
    allowed_facts: list[Fact] = []

    if reply_line is not None and win_before - win_after >= THREAT_MIN_SWING:
        allowed_facts = _new_facts_after(
            board_after, board_after, reply_line.pv[0], side_of(board_after.turn)
        )
        facts.append(_allows_fact(board_after, reply_line, win_before - win_after, allowed_facts))

    if threats.move is not None and board_after.is_legal(threats.move):
        swing = await _threat_swing(board_after, threats.move, after, pool)
        if swing >= THREAT_MIN_SWING:
            facts.append(_ignored_threat_fact(board, threats, threats.move, swing))

    return MoveDelta(
        move_san=move_san,
        win_probability_before=win_before,
        win_probability_after=win_after,
        best_move=best_line[0] if best_line else None,
        best_move_san=_sans(board, best_line[:1])[0] if best_line else None,
        best_line=_sans(board, best_line),
        reply=reply_line.pv[0] if reply_line else None,
        reply_san=_sans(board_after, reply_line.pv[:1])[0] if reply_line else None,
        allowed_facts=allowed_facts,
        facts=facts,
    )
