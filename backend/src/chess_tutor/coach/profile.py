"""What the coach knows about the player's history with the concepts in play (spec 6.5).

Each concept carries its state, its count and a style directive the coach follows, plus the last
few phrasings already used for it so the coach varies its wording.
"""

import json
import re
from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from chess_tutor.concepts.taxonomy import CONCEPTS, Concept
from chess_tutor.models import CoachMessage, CoachRole, ConceptState, ConceptStatus, Game

RECENT_PHRASINGS_PER_CONCEPT = 3
PHRASING_LOOKBACK_MESSAGES = 50  # newest coach messages scanned across the player's games


class ConceptProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    concept: Concept
    label: str
    state: ConceptStatus
    count: int  # finished games in which the concept occurred
    style: str
    recent_phrasings: list[str] = Field(default_factory=list)


class CoachProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    concepts: list[ConceptProfile]


def style_directive(concept: Concept, status: ConceptStatus, count: int) -> str:
    match status:
        case ConceptStatus.NEW:
            return "First time seen: explain it fully."
        case ConceptStatus.RECURRING:
            return (
                f"This has now come up in {count} games: keep it short, ask a Socratic question "
                "instead of explaining, and mention that it keeps happening."
            )
        case ConceptStatus.FOCUS:
            theme = re.sub(r"(?<!^)(?=[A-Z])", " ", CONCEPTS[concept].practice_themes[0]).lower()
            return (
                "This is the player's current focus: say so, keep it short, and suggest "
                f"practising {theme} puzzles."
            )
        case ConceptStatus.IMPROVING:
            return "The player is improving here: one brief, encouraging line, little commentary."
        case ConceptStatus.MASTERED:
            return 'The player had mastered this and it is back: say "this one\'s back" briefly.'


def build_coach_profile(
    session: Session,
    player_id: int,
    concepts_in_play: Sequence[Concept],
    recent_phrasings: Mapping[Concept, Sequence[str]],
) -> CoachProfile:
    """Profiles for the concepts behind the current move plus the player's FOCUS concepts.

    A MASTERED concept appears only when it is in play, i.e. when the mistake recurred.
    """
    rows = {
        row.concept: row
        for row in session.scalars(select(ConceptState).where(ConceptState.player_id == player_id))
    }
    focus = [Concept(name) for name, row in rows.items() if row.state is ConceptStatus.FOCUS]
    entries = []
    for concept in dict.fromkeys([*concepts_in_play, *focus]):
        row = rows.get(concept.value)
        status, count = (row.state, row.count) if row else (ConceptStatus.NEW, 0)
        entries.append(
            ConceptProfile(
                concept=concept,
                label=CONCEPTS[concept].label,
                state=status,
                count=count,
                style=style_directive(concept, status, count),
                recent_phrasings=list(recent_phrasings.get(concept, ()))[
                    -RECENT_PHRASINGS_PER_CONCEPT:
                ],
            )
        )
    return CoachProfile(concepts=entries)


def stored_phrasings(session: Session, player_id: int) -> dict[Concept, list[str]]:
    """The coach's recent wording per concept, oldest first, from this player's stored messages."""
    rows = session.execute(
        select(CoachMessage.text, CoachMessage.concepts)
        .join(Game, Game.id == CoachMessage.game_id)
        .where(Game.player_id == player_id, CoachMessage.role == CoachRole.COACH)
        .order_by(CoachMessage.id.desc())
        .limit(PHRASING_LOOKBACK_MESSAGES)
    )
    phrasings: dict[Concept, list[str]] = {}
    for text, concepts in reversed(rows.all()):
        for name in json.loads(concepts):
            phrasings.setdefault(Concept(name), []).append(text)
    return phrasings
