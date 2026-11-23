"""Per-player concept state machine with exponential recency decay (spec 6.5).

Every finished game moves each tracked concept once. `recency_score` is multiplied by
`recency_decay` (0.8) for every finished game, and gains 1 when the concept occurred in it, so a
concept seen a few games ago weighs less than one seen last game. `games_since_last_seen` is the
current clean-game streak (0 right after an occurrence).

On an occurrence (`count` += 1, streak reset):
- IMPROVING or MASTERED drops back to RECURRING (the demotion);
- NEW becomes RECURRING once `count` >= recurring_after and `recency_score` >= recurring_min_score;
- RECURRING becomes FOCUS once `count` >= focus_after and `recency_score` >= focus_min_score.
  A demoted concept can therefore jump straight to FOCUS only when it is frequent again.

On a clean game (the concept did not occur in a finished game):
- RECURRING or FOCUS becomes IMPROVING after improving_after_clean_games clean games in a row;
- IMPROVING becomes MASTERED after mastered_after_clean_games clean games in a row;
- NEW stays NEW: a single slip is not yet a pattern.

All thresholds live in `Thresholds.concept_state`.
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from chess_tutor.models import ConceptEvent, ConceptState, ConceptStatus
from chess_tutor.settings import ConceptStateThresholds

# shortcut: "clean" means any finished game without the concept, even one where it could not
# arise; track per-concept opportunity if mastery comes too cheaply.


def new_state(player_id: int, concept: str) -> ConceptState:
    return ConceptState(
        player_id=player_id,
        concept=concept,
        state=ConceptStatus.NEW,
        count=0,
        recency_score=0.0,
        last_seen_game_id=None,
        games_since_last_seen=0,
        last_processed_game_id=0,
    )


def update_on_occurrence(
    row: ConceptState, game_id: int, now: datetime, thresholds: ConceptStateThresholds
) -> None:
    row.count += 1
    row.recency_score = row.recency_score * thresholds.recency_decay + 1
    row.games_since_last_seen = 0
    row.last_seen_game_id = game_id
    row.last_processed_game_id = game_id
    row.updated_at = now
    if row.state in (ConceptStatus.IMPROVING, ConceptStatus.MASTERED) or (
        row.state is ConceptStatus.NEW
        and row.count >= thresholds.recurring_after
        and row.recency_score >= thresholds.recurring_min_score
    ):
        row.state = ConceptStatus.RECURRING
    if (
        row.state is ConceptStatus.RECURRING
        and row.count >= thresholds.focus_after
        and row.recency_score >= thresholds.focus_min_score
    ):
        row.state = ConceptStatus.FOCUS


def update_on_clean_game(
    row: ConceptState, game_id: int, now: datetime, thresholds: ConceptStateThresholds
) -> None:
    row.recency_score *= thresholds.recency_decay
    row.games_since_last_seen += 1
    row.last_processed_game_id = game_id
    row.updated_at = now
    if (
        row.state in (ConceptStatus.RECURRING, ConceptStatus.FOCUS)
        and row.games_since_last_seen >= thresholds.improving_after_clean_games
    ):
        row.state = ConceptStatus.IMPROVING
    elif (
        row.state is ConceptStatus.IMPROVING
        and row.games_since_last_seen >= thresholds.mastered_after_clean_games
    ):
        row.state = ConceptStatus.MASTERED


def refresh_concept_states(
    session: Session,
    player_id: int,
    game_id: int,
    now: datetime,
    thresholds: ConceptStateThresholds,
) -> None:
    """Apply one finished game to the player's concept states; the caller commits.

    Safe to call twice for the same game. Games must finish in id order per player: a row already
    past `game_id` is left alone.
    """
    occurred = set(
        session.scalars(
            select(ConceptEvent.concept)
            .where(ConceptEvent.player_id == player_id, ConceptEvent.game_id == game_id)
            .distinct()
        )
    )
    rows = {
        row.concept: row
        for row in session.scalars(select(ConceptState).where(ConceptState.player_id == player_id))
    }
    for concept in occurred - rows.keys():
        rows[concept] = new_state(player_id, concept)
        session.add(rows[concept])
    for concept, row in rows.items():
        if row.last_processed_game_id >= game_id:
            continue
        if concept in occurred:
            update_on_occurrence(row, game_id, now, thresholds)
        else:
            update_on_clean_game(row, game_id, now, thresholds)
