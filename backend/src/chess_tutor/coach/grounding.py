"""Grounding: the coach never states a chess fact the fact packet did not verify (spec 6.4).

Rules `validate_reply` enforces on `reply.text`:
- every id in `fact_ids` is among the facts the model was shown (after pre-lock-in filtering);
- a SAN move (piece moves, pawn captures, promotions, castling; check marks ignored) must appear
  in a cited fact's `san_lines`, and that line must replay legally with python-chess from the
  packet position, from the position after the move being commented on, or from the same
  position with the opponent to move (threat facts). The move being commented on and, after
  lock-in, the best move and line are also allowed;
- a bare square ("e4", matched only as a standalone [a-h][1-8] token) must appear in a cited
  fact's `squares` or `pieces`; a bare token that equals a verified pawn move is accepted as
  that move;
- "<piece> on <square>" must also match a cited fact's piece type when that fact lists a piece
  on the square;
- Active mode before lock-in: the best move's SAN may not appear at all;
- text with no concrete move or square needs no fact.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum

import chess

from chess_tutor.coach.llm import (
    ChatMessage,
    LanguageModel,
    LanguageModelError,
    ModelTier,
    Usage,
)
from chess_tutor.coach.prompt import (
    CoachContext,
    CoachEvent,
    CoachReply,
    build_coach_request,
    generate_coach_reply,
    visible_facts,
)
from chess_tutor.facts.models import Fact, FactType, PieceRef

_SQUARE = r"[a-h][1-8]"
_SAN = re.compile(
    rf"(?<![\w-])(?:O-O-O|O-O|0-0-0|0-0"
    rf"|[KQRBN][a-h]?[1-8]?x?{_SQUARE}"
    rf"|[a-h]x{_SQUARE}(?:=[QRBN])?|{_SQUARE}=[QRBN])[+#]?(?!\w)"
)
_SQUARE_TOKEN = re.compile(rf"(?<![\w-])({_SQUARE})(?!\w)")
_PIECE_ON_SQUARE = re.compile(
    rf"\b(king|queen|rook|bishop|knight|pawn)s?\s+(?:on|at)\s+({_SQUARE})(?!\w)", re.IGNORECASE
)
_NO_CHECK_MARK = str.maketrans("", "", "+#")

FACT_LABELS: dict[FactType, str] = {
    FactType.MATERIAL_BALANCE: "material balance worth keeping in mind",
    FactType.HANGING_PIECE: "piece that is not safe",
    FactType.FORK: "fork pattern",
    FactType.PIN: "pin",
    FactType.SKEWER: "skewer",
    FactType.DISCOVERED_ATTACK: "discovered attack",
    FactType.KING_SAFETY: "king safety issue",
    FactType.PAWN_STRUCTURE: "pawn structure point",
    FactType.DEVELOPMENT: "development point",
    FactType.ACTIVITY: "piece activity point",
    FactType.BACK_RANK_WEAKNESS: "back rank weakness",
    FactType.MATE_THREAT: "mating threat",
    FactType.MATE_IN_ONE: "chance to give checkmate",
    FactType.OVERLOADED_DEFENDER: "defender doing too many jobs",
    FactType.TRAPPED_PIECE: "piece with no safe square",
    FactType.ALLOWS: "reply to watch for",
    FactType.IGNORED_THREAT: "threat that was left standing",
}
LEVEL_ONE_NUDGE = "Before you commit, ask yourself: what can your opponent do next?"
NO_FACT_TEMPLATE = "Take a moment to check what your opponent threatens before you move."
THOUGHT_QUESTION = "What were you thinking when you played {move}?"
_TEMPLATE_PRIORITY = {FactType.ALLOWS, FactType.IGNORED_THREAT}


class GroundingErrorKind(StrEnum):
    UNKNOWN_FACT = "unknown_fact"
    UNCITED_SQUARE = "uncited_square"
    UNVERIFIED_MOVE = "unverified_move"
    PIECE_MISMATCH = "piece_mismatch"
    BEST_MOVE_LEAK = "best_move_leak"


@dataclass(frozen=True)
class GroundingError:
    kind: GroundingErrorKind
    message: str


class ReplySource(StrEnum):
    LLM = "llm"
    LLM_REGENERATED = "llm_regenerated"
    TEMPLATE = "template"


@dataclass(frozen=True)
class GroundedReply:
    text: str
    fact_ids: list[str]
    source: ReplySource
    usages: tuple[Usage, ...]  # every billed attempt, including rejected ones
    first_attempt_errors: tuple[GroundingError, ...] = ()
    first_attempt_text: str = ""  # what the model said before it was rejected

    @property
    def cost(self) -> float:
        return sum(usage.cost for usage in self.usages)


def _plain(san: str) -> str:
    return san.translate(_NO_CHECK_MARK)


def _start_positions(context: CoachContext) -> Iterator[chess.Board]:
    """Where a fact's line may start: the packet position, after the commented move, or with the
    opponent to move (threat facts are computed as if the opponent had a free turn)."""
    board = chess.Board(context.fact_packet.fen)
    yield board
    after_move = board.copy(stack=False)
    try:
        after_move.push_san(context.move_san)
    except ValueError:
        pass
    else:
        yield after_move
    if not board.is_check():
        skipped = board.copy(stack=False)
        skipped.push(chess.Move.null())
        yield skipped.copy(stack=False)


def _replay(line: list[str], start: chess.Board) -> list[chess.Move] | None:
    board = start.copy(stack=False)
    moves = []
    try:
        for san in line:
            moves.append(board.push_san(san))
    except ValueError:
        return None
    return moves


def _verified_references(cited: list[Fact], context: CoachContext) -> tuple[set[str], set[str]]:
    """The SANs and the squares those verified moves touch (from and to)."""
    starts = list(_start_positions(context))
    lines = [[context.move_san]]
    if not context.active_pre_lock_in:  # the prompt makes best_move/best_line citable once shown
        lines += [context.best_line, [context.best_move_san] if context.best_move_san else []]
    lines += [line for fact in cited for line in fact.san_lines]
    sans: set[str] = set()
    squares: set[str] = set()
    for line in lines:
        for start in starts:
            moves = _replay(line, start)
            if moves is not None:
                sans.update(_plain(san) for san in line)
                squares.update(
                    chess.square_name(square)
                    for move in moves
                    for square in (move.from_square, move.to_square)
                )
                break
    return sans, squares


def _cited_squares(cited: list[Fact]) -> set[str]:
    return {
        square for fact in cited for square in [*fact.squares, *(p.square for p in fact.pieces)]
    }


def _cited_pieces(cited: list[Fact]) -> list[PieceRef]:
    return [piece for fact in cited for piece in fact.pieces]


def _error(kind: GroundingErrorKind, message: str) -> GroundingError:
    return GroundingError(kind, message)


def validate_reply(reply: CoachReply, context: CoachContext) -> list[GroundingError]:
    shown = {fact.id: fact for fact in visible_facts(context)}
    errors = [
        _error(
            GroundingErrorKind.UNKNOWN_FACT,
            f"Your reply cited fact id {fact_id!r}, which does not exist. Cite only given ids.",
        )
        for fact_id in reply.fact_ids
        if fact_id not in shown
    ]
    cited = [shown[fact_id] for fact_id in reply.fact_ids if fact_id in shown]
    text = reply.text
    mentioned_moves = [match.group() for match in _SAN.finditer(text)]
    bare_text = _SAN.sub(" ", text)

    if context.active_pre_lock_in and context.best_move_san:
        best = re.escape(_plain(context.best_move_san))
        if re.search(rf"(?<![\w-]){best}[+#]?(?!\w)", text):
            errors.append(
                _error(
                    GroundingErrorKind.BEST_MOVE_LEAK,
                    "Your reply names the best move, which the student must find alone. "
                    "Remove it and do not hint at a specific move.",
                )
            )

    verified, move_squares = _verified_references(cited, context)
    for move in mentioned_moves:
        if _plain(move) not in verified:
            errors.append(
                _error(
                    GroundingErrorKind.UNVERIFIED_MOVE,
                    f"Your reply mentioned the move {move}, which is not a verified move in any "
                    "cited fact. Rewrite it using only the cited facts.",
                )
            )

    squares = _cited_squares(cited) | move_squares
    for square in dict.fromkeys(_SQUARE_TOKEN.findall(bare_text)):
        if square not in squares and square not in verified:
            errors.append(
                _error(
                    GroundingErrorKind.UNCITED_SQUARE,
                    f"Your reply mentioned {square}, which is not in any cited fact. "
                    "Rewrite it using only the cited facts.",
                )
            )

    pieces = _cited_pieces(cited)
    for name, square in _PIECE_ON_SQUARE.findall(bare_text):
        types_there = {piece.piece_type for piece in pieces if piece.square == square}
        if types_there and name.lower() not in types_there:
            errors.append(
                _error(
                    GroundingErrorKind.PIECE_MISMATCH,
                    f"Your reply put a {name.lower()} on {square}, but the cited facts have a "
                    f"{' or '.join(sorted(types_there))} there. Rewrite it using only the cited "
                    "facts.",
                )
            )
    return errors


def _describe(pieces: list[PieceRef]) -> str:
    return " and ".join(f"the {piece.piece_type} on {piece.square}" for piece in pieces)


def _fact_template(fact: Fact, context: CoachContext, level: int | None) -> str:
    line = fact.san_lines[0] if fact.san_lines else []
    if fact.type is FactType.ALLOWS and line:
        return f"Careful: after {context.move_san}, your opponent has {line[0]}."
    if fact.type is FactType.IGNORED_THREAT and line:
        return (
            f"Careful: your opponent threatens {line[0]}, "
            f"and {context.move_san} did not deal with it."
        )
    label = FACT_LABELS[fact.type]
    shown = fact.pieces[:1] if level == 2 else fact.pieces
    if shown:
        return f"Take a look at {_describe(shown)}: there is a {label} here."
    if fact.squares:
        squares = fact.squares[:1] if level == 2 else fact.squares
        return f"Take a look at {' and '.join(squares)}: there is a {label} here."
    return f"Something to keep in mind: a {label}."


def template_reply(context: CoachContext) -> CoachReply:
    """A deterministic reply built from verified facts only; it always passes `validate_reply`."""
    if context.hint_level == 1:
        return CoachReply(text=LEVEL_ONE_NUDGE, fact_ids=[])
    if context.event is CoachEvent.ASK_THOUGHT:
        return CoachReply(text=THOUGHT_QUESTION.format(move=context.move_san), fact_ids=[])
    facts = visible_facts(context)
    if not facts:
        return CoachReply(text=NO_FACT_TEMPLATE, fact_ids=[])
    chosen = max(facts, key=lambda fact: (fact.type in _TEMPLATE_PRIORITY, fact.severity))
    return CoachReply(
        text=_fact_template(chosen, context, context.hint_level), fact_ids=[chosen.id]
    )


def _feedback(errors: list[GroundingError]) -> str:
    return " ".join(error.message for error in errors)


async def coach_reply_with_grounding(model: LanguageModel, context: CoachContext) -> GroundedReply:
    """Generate, validate, regenerate once with the errors as feedback, else use the template."""
    usages: list[Usage] = []
    first_errors: tuple[GroundingError, ...] = ()
    first_text = ""
    try:
        first = await generate_coach_reply(model, context)
        usages.append(first.usage)
        errors = validate_reply(first.parsed, context)
        if not errors:
            return GroundedReply(
                first.parsed.text, first.parsed.fact_ids, ReplySource.LLM, tuple(usages)
            )
        first_errors, first_text = tuple(errors), first.parsed.text
        system, messages = build_coach_request(context)
        second = await model.structured(
            tier=ModelTier.COACHING,
            system=system,
            messages=[
                *messages,
                ChatMessage("assistant", first.parsed.model_dump_json()),
                ChatMessage("user", _feedback(errors)),
            ],
            schema=CoachReply,
        )
        usages.append(second.usage)
        if not validate_reply(second.parsed, context):
            return GroundedReply(
                second.parsed.text,
                second.parsed.fact_ids,
                ReplySource.LLM_REGENERATED,
                tuple(usages),
                first_errors,
                first_text,
            )
    except LanguageModelError as error:
        if error.usage is not None:
            usages.append(error.usage)
    fallback = template_reply(context)
    return GroundedReply(
        fallback.text, fallback.fact_ids, ReplySource.TEMPLATE, tuple(usages), first_errors
    )
