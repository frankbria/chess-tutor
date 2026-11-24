"""Scoring for the coaching eval: curated cases, context building and the four pass dimensions."""

import json
import re
from dataclasses import dataclass
from pathlib import Path

import chess
from pydantic import BaseModel

from chess_tutor.analysis.move_delta import MoveDelta
from chess_tutor.coach.grounding import GroundedReply, ReplySource, validate_reply
from chess_tutor.coach.prompt import (
    CoachContext,
    CoachEvent,
    CoachReply,
    build_coach_request,
    visible_facts,
)
from chess_tutor.facts.models import FactType
from chess_tutor.facts.packet import build_fact_packet
from chess_tutor.models import GameMode
from chess_tutor.openings.phase import GamePhase

CASES_PATH = Path(__file__).with_name("cases.json")
SQUARE = r"[a-h][1-8]"
_SQUARE_TOKEN = re.compile(rf"(?<![\w-])({SQUARE})(?!\w)")
_PIECE_ON_SQUARE = re.compile(
    rf"\b(king|queen|rook|bishop|knight|pawn)s?\s+(?:on|at)\s+({SQUARE})(?!\w)", re.IGNORECASE
)
_META_TALK = re.compile(
    r"^\W*(?:good catch|let me (?:redo|rewrite|fix|try again)|sorry|my (?:mistake|apologies)"
    r"|i apologi[sz]e)|\b(?:correction|rewrit(?:e|ing|ten)|(?:my )?(?:previous|earlier) reply"
    r"|the facts|fact ids?|the context|the engine|stockfish|my data)\b",
    re.IGNORECASE,
)


class EvalCase(BaseModel):
    id: str
    phase: GamePhase
    mode: GameMode
    event: CoachEvent
    fen: str
    move: str
    hint_level: int | None = None
    player_message: str | None = None
    locked_in: bool = False
    expect_any: list[str]
    forbid: list[str]

    @property
    def active_pre_lock_in(self) -> bool:
        return self.mode is GameMode.ACTIVE and not self.locked_in


def load_cases() -> list[EvalCase]:
    return [EvalCase.model_validate(raw) for raw in json.loads(CASES_PATH.read_text())]


def build_context(case: EvalCase, delta: MoveDelta) -> CoachContext:
    return CoachContext(
        mode=case.mode,
        active_pre_lock_in=case.active_pre_lock_in,
        phase=case.phase,
        event=case.event,
        move_san=case.move,
        fact_packet=build_fact_packet(chess.Board(case.fen)),
        move_delta_facts=delta.facts,
        best_move_san=delta.best_move_san,
        best_line=delta.best_line,
        hint_level=case.hint_level,
        player_message=case.player_message,
    )


_FACT_TYPE_VALUES = frozenset(fact_type.value for fact_type in FactType)


def concept_mentioned(expect_any: list[str], text: str, cited_types: set[FactType]) -> bool:
    """Loose match: any expectation that is a fact type must be cited, otherwise it is a regex."""
    if not expect_any:
        return True
    fact_types = {fact_type.value for fact_type in cited_types}
    return any(
        (
            expected in fact_types
            if expected in _FACT_TYPE_VALUES
            else re.search(expected, text, re.IGNORECASE) is not None
        )
        for expected in expect_any
    )


def _plain(san: str) -> str:
    return san.rstrip("+#")


def _allowed_squares(context: CoachContext) -> set[str]:
    _, messages = build_coach_request(context)
    allowed = json.loads(messages[0].content)["allowed_references"]
    from_moves = (square for move in allowed["moves"] for square in re.findall(SQUARE, move))
    return {*allowed["squares"], *from_moves, *_own_move_squares(context)}


def _own_move_squares(context: CoachContext) -> set[str]:
    board = chess.Board(context.fact_packet.fen)
    move = board.parse_san(context.move_san)
    return {chess.square_name(move.from_square), chess.square_name(move.to_square)}


def _piece_shown(context: CoachContext, piece: str, square: str) -> bool:
    board = chess.Board(context.fact_packet.fen)
    after_move = board.copy(stack=False)
    after_move.push_san(context.move_san)
    on_boards = (
        (found := position.piece_at(chess.parse_square(square))) is not None
        and chess.piece_name(found.piece_type) == piece
        for position in (board, after_move)
    )
    on_facts = (
        ref.piece_type == piece and ref.square == square
        for fact in visible_facts(context)
        for ref in fact.pieces
    )
    return any(on_boards) or any(on_facts)


def forbidden_claims(text: str, context: CoachContext, forbid: list[str]) -> list[str]:
    """Independent of the validator: what the reply must never say, as short violation labels."""
    found: list[str] = []
    best = context.best_move_san
    hidden_best = context.active_pre_lock_in and best and _plain(best) != _plain(context.move_san)
    if hidden_best and best and re.search(rf"(?<![\w-]){re.escape(_plain(best))}[+#]?(?!\w)", text):
        found.append(f"best_move:{best}")
    squares = list(dict.fromkeys(_SQUARE_TOKEN.findall(text)))
    others = [square for square in squares if square not in _own_move_squares(context)]
    if context.active_pre_lock_in and context.hint_level == 1:
        found += [f"square_in_level_1:{square}" for square in squares]
    if context.active_pre_lock_in and context.hint_level == 2 and len(others) > 1:
        found.append("too_many_squares_in_level_2")
    allowed = _allowed_squares(context)
    found += [f"unknown_square:{square}" for square in squares if square not in allowed]
    for piece, square in _PIECE_ON_SQUARE.findall(text):
        if not _piece_shown(context, piece.lower(), square):
            found.append(f"false_piece:{piece.lower()} on {square}")
    if _META_TALK.search(text):
        found.append("meta_talk")
    found += [
        f"forbidden_phrase:{pattern}"
        for pattern in forbid
        if re.search(pattern, text, re.IGNORECASE)
    ]
    return found


@dataclass(frozen=True)
class CaseScore:
    case_id: str
    grounded_first_attempt: bool
    validator_passes: bool
    concept_mentioned: bool
    no_forbidden_claim: bool
    violations: list[str]
    text: str
    cost: float
    seconds: float

    @property
    def passes(self) -> bool:
        return (
            self.grounded_first_attempt
            and self.validator_passes
            and self.concept_mentioned
            and self.no_forbidden_claim
        )


def score_case(
    case: EvalCase, context: CoachContext, grounded: GroundedReply, seconds: float
) -> CaseScore:
    shown = {fact.id: fact.type for fact in visible_facts(context)}
    cited_types = {shown[fact_id] for fact_id in grounded.fact_ids if fact_id in shown}
    violations = forbidden_claims(grounded.text, context, case.forbid)
    reply = CoachReply(text=grounded.text, fact_ids=grounded.fact_ids)
    return CaseScore(
        case_id=case.id,
        grounded_first_attempt=grounded.source is ReplySource.LLM,
        validator_passes=not validate_reply(reply, context),
        concept_mentioned=concept_mentioned(case.expect_any, grounded.text, cited_types),
        no_forbidden_claim=not violations,
        violations=violations,
        text=grounded.text,
        cost=grounded.cost,
        seconds=seconds,
    )


DIMENSIONS = {
    "grounded_first_attempt": "grounded on first attempt",
    "validator_passes": "passes validator",
    "concept_mentioned": "mentions expected concept",
    "no_forbidden_claim": "no forbidden claim",
}


def summarize(scores: list[CaseScore]) -> str:
    total = len(scores)
    lines = [f"Coaching eval: {sum(s.passes for s in scores)}/{total} cases passed all dimensions"]
    for attribute, label in DIMENSIONS.items():
        passed = sum(getattr(score, attribute) for score in scores)
        lines.append(f"  {label}: {passed}/{total} ({passed / total:.0%})")
    lines.append(f"  total cost: ${sum(s.cost for s in scores):.4f}")
    lines.append(f"  total time: {sum(s.seconds for s in scores):.0f}s")
    for score in scores:
        if not score.passes:
            failed = [label for attr, label in DIMENSIONS.items() if not getattr(score, attr)]
            lines.append(f"  FAIL {score.case_id}: {', '.join(failed)} {score.violations}")
            lines.append(f"       {score.text}")
    return "\n".join(lines)
