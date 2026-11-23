"""The coach's input and output: a static system prompt plus a per-request context message.

Everything that varies per request lives in the user message, so the system prompt is
byte-identical across requests and prompt caching applies (spec 6.6).
"""

import json
import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from chess_tutor.coach.llm import ChatMessage, LanguageModel, ModelTier, StructuredReply
from chess_tutor.facts.models import Fact, FactPacket
from chess_tutor.models import GameMode
from chess_tutor.openings.phase import PHASE_FOCUS, GamePhase

RECENT_MESSAGES_SHOWN = 5

SYSTEM_PROMPT = """\
You are a chess coach inside a tutoring app. Your student is an adult playing at roughly 800 \
rating. Be warm, brief and encouraging, in plain words with little jargon. Replies are at most \
about 60 words unless the student asked a question that needs more.

Each request arrives as a JSON context. You may name a concrete square, piece or move only if it \
appears in the context's facts, is the move under discussion (`move`), or, when present, is the \
`best_move` or `best_line`. Everything else is off limits. Every fact you rely on must have its \
id in `fact_ids`. Never invent chess facts. General advice with no concrete square or move \
("develop your pieces", "check what your opponent threatens") needs no fact.

Fit your reply to `event`:
- comment_on_move: say what matters about `move`, using the facts. If it was fine, say so in a \
few words.
- hint: the student is rethinking `move` before committing, and `hint_level` says how much to \
give. Level 1 (nudge): point at a direction only, as a question such as what the opponent can \
do next; name no square. Level 2 (pointer): name at most one square or piece from the facts. \
Level 3 (consequence): you may state the verified outcome from the facts.
- answer_question: answer `player_message` from the facts, at whatever length it needs. When \
`mode` is active and `locked_in` is false, follow the hint level rules above exactly as for a \
hint, even though the student asked a question.
- ask_thought: ask one short question about what the student was thinking.

When `player_message` gives the student's reasoning, respond to it: "Exactly right" when it \
holds, "I see the idea, but..." or "Not a bad idea, but you're missing..." when a fact shows \
something it overlooks.

When `mode` is active and `locked_in` is false, the best move is not in the context and you must \
not suggest or hint at a specific one. Guide with questions and what the facts show.

At higher `repetition` for a concept, be shorter and ask Socratic questions instead of \
explaining. Vary your wording from `recent_coach_messages`. The phase's `focus` list is a menu \
of things worth noticing, not a checklist.\
"""


class CoachEvent(StrEnum):
    COMMENT_ON_MOVE = "comment_on_move"
    HINT = "hint"
    ANSWER_QUESTION = "answer_question"
    ASK_THOUGHT = "ask_thought"


class CoachContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    mode: GameMode
    # Required on purpose: a forgotten flag must fail loudly, not leak the best move.
    active_pre_lock_in: bool
    phase: GamePhase
    event: CoachEvent
    move_san: str
    fact_packet: FactPacket
    move_delta_facts: list[Fact] = Field(default_factory=list)
    best_move_san: str | None = None
    best_line: list[str] = Field(default_factory=list)  # SAN, starting with the best move
    hint_level: int | None = Field(default=None, ge=1, le=3)  # Active mode only
    profile_summary: str = ""
    recent_coach_messages: list[str] = Field(default_factory=list)
    player_message: str | None = None


class CoachReply(BaseModel):
    text: str
    fact_ids: list[str]


def _without_suffix(san: str) -> str:
    return san.rstrip("+#")


def _leaks_best_move(fact: Fact, best_move_san: str) -> bool:
    """The best move anywhere in a fact's lines, or as a SAN token in its summary.

    Check and mate suffixes are ignored, and the summary match tolerates surrounding punctuation.
    """
    best = _without_suffix(best_move_san)
    in_lines = any(_without_suffix(move) == best for line in fact.san_lines for move in line)
    token = rf"(?<![A-Za-z0-9\-]){re.escape(best)}(?![A-Za-z0-9=]|-O)"
    return in_lines or re.search(token, fact.summary) is not None


def visible_facts(context: CoachContext) -> list[Fact]:
    facts = [*context.fact_packet.facts, *context.move_delta_facts]
    if context.active_pre_lock_in and context.best_move_san:
        # A fact such as a fork opportunity can BE the best move; dropping only the best-move
        # fields would still leak it through the fact's line or summary.
        facts = [fact for fact in facts if not _leaks_best_move(fact, context.best_move_san)]
    return facts


def _context_payload(context: CoachContext) -> dict[str, object]:
    payload: dict[str, object] = {
        "mode": context.mode.value,
        "event": context.event.value,
        "phase": context.phase.value,
        "focus": PHASE_FOCUS[context.phase],
        "move": context.move_san,
        "fen": context.fact_packet.fen,
        "side_to_move": context.fact_packet.side_to_move.value,
        "facts": [
            fact.model_dump(mode="json", exclude_defaults=True) for fact in visible_facts(context)
        ],
        "locked_in": not context.active_pre_lock_in,
    }
    if not context.active_pre_lock_in:  # pre-lock-in the best move is removed, not hidden
        payload["best_move"] = context.best_move_san
        payload["best_line"] = context.best_line
    optional: dict[str, object] = {
        "hint_level": context.hint_level,
        "profile": context.profile_summary,
        "recent_coach_messages": context.recent_coach_messages[-RECENT_MESSAGES_SHOWN:],
        "player_message": context.player_message,
    }
    payload.update({key: value for key, value in optional.items() if value})
    return payload


def build_coach_request(context: CoachContext) -> tuple[str, list[ChatMessage]]:
    """The static system prompt and one user message carrying the context as JSON.

    When `active_pre_lock_in` is set, `best_move`/`best_line` are omitted entirely and every fact
    containing the best move in any SAN line (or naming it in its summary) is dropped.
    """
    content = json.dumps(_context_payload(context), separators=(",", ":"))
    return SYSTEM_PROMPT, [ChatMessage("user", content)]


async def generate_coach_reply(
    model: LanguageModel, context: CoachContext
) -> StructuredReply[CoachReply]:
    system, messages = build_coach_request(context)
    return await model.structured(
        tier=ModelTier.COACHING, system=system, messages=messages, schema=CoachReply
    )
