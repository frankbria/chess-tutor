"""Structured summary of a finished game, kept so the coach can remember it later (spec 6.5).

The cheap model writes it from data we already hold. With no model, or when the model fails, a
summary built from the same data is stored instead, so every finished game gets one.
"""

import json
from dataclasses import dataclass

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from chess_tutor.coach.llm import ChatMessage, LanguageModel, LanguageModelError, ModelTier, Usage
from chess_tutor.concepts.taxonomy import CONCEPTS, Concept
from chess_tutor.models import (
    CoachMessageSource,
    ConceptEvent,
    Game,
    GameResult,
    GameSummary,
    Move,
    PlayerColor,
)

NOTABLE_CLASSIFICATIONS = ("mistake", "blunder", "great", "brilliant")

SUMMARY_SYSTEM_PROMPT = """\
You write a short memory note about one finished chess game, for the student's coach to read \
before later games. The context is JSON: the opening, the result for the student, the moments \
that mattered (move number and how the move was rated) and the recurring-mistake concepts that \
were tagged. Write `summary`: two plain sentences covering the opening, how the game went and \
the result. Write `key_moments`: at most three short sentences, each about one moment or concept. \
Never name a square, piece move or move notation; refer to moments by move number and concept. \
Never invent anything that is not in the context.\
"""


class SummaryDraft(BaseModel):
    summary: str
    key_moments: list[str]


@dataclass(frozen=True)
class NotableMove:
    move_number: int
    classification: str


@dataclass(frozen=True)
class GameDigest:
    opening: str | None
    outcome: str  # for the student: won, lost or drew
    color: PlayerColor
    on_time: bool
    notable_moves: list[NotableMove]
    concepts: list[Concept]  # in the order they first occurred


def _outcome(game: Game) -> str:
    if game.result is GameResult.DRAW:
        return "drew"
    player_won = (game.result is GameResult.WHITE_WINS) == (game.player_color is PlayerColor.WHITE)
    return "won" if player_won else "lost"


def build_digest(session: Session, game: Game) -> GameDigest:
    player_parity = 1 if game.player_color is PlayerColor.WHITE else 0
    moves = session.scalars(select(Move).where(Move.game_id == game.id).order_by(Move.ply))
    notable = [
        NotableMove((move.ply + 1) // 2, move.classification)
        for move in moves
        if move.ply % 2 == player_parity and move.classification in NOTABLE_CLASSIFICATIONS
    ]
    names = session.scalars(
        select(ConceptEvent.concept)
        .where(ConceptEvent.game_id == game.id)
        .order_by(ConceptEvent.ply)
    )
    return GameDigest(
        opening=game.opening_name,
        outcome=_outcome(game),
        color=game.player_color,
        on_time=game.ended_on_time,
        notable_moves=notable,
        concepts=[Concept(name) for name in dict.fromkeys(names)],
    )


def template_summary(digest: GameDigest) -> SummaryDraft:
    opening = digest.opening or "an unnamed opening"
    ending = " on time" if digest.on_time else ""
    summary = f"Playing {digest.color.value} in {opening}, you {digest.outcome}{ending}."
    if digest.concepts:
        labels = ", ".join(CONCEPTS[concept].label for concept in digest.concepts)
        summary += f" Mistake patterns tagged: {labels}."
    moments = [
        f"Move {move.move_number} was rated a {move.classification}."
        for move in digest.notable_moves
    ]
    return SummaryDraft(summary=summary, key_moments=moments)


def _request(digest: GameDigest) -> list[ChatMessage]:
    payload = {
        "opening": digest.opening,
        "result_for_student": digest.outcome + (" on time" if digest.on_time else ""),
        "student_color": digest.color.value,
        "moments": [
            {"move_number": move.move_number, "rating": move.classification}
            for move in digest.notable_moves
        ],
        "concepts": [CONCEPTS[concept].label for concept in digest.concepts],
    }
    return [ChatMessage("user", json.dumps(payload, separators=(",", ":")))]


async def summarize_game(
    model: LanguageModel | None, digest: GameDigest
) -> tuple[SummaryDraft, CoachMessageSource, list[Usage]]:
    """The draft, where it came from, and every billed usage."""
    if model is None:
        return template_summary(digest), CoachMessageSource.TEMPLATE, []
    try:
        reply = await model.structured(
            tier=ModelTier.CHEAP,
            system=SUMMARY_SYSTEM_PROMPT,
            messages=_request(digest),
            schema=SummaryDraft,
        )
    except LanguageModelError as error:
        usages = [error.usage] if error.usage else []
        return template_summary(digest), CoachMessageSource.TEMPLATE, usages
    return reply.parsed, CoachMessageSource.LLM, [reply.usage]


def store_summary(
    session: Session,
    game: Game,
    digest: GameDigest,
    draft: SummaryDraft,
    source: CoachMessageSource,
) -> None:
    session.add(
        GameSummary(
            game_id=game.id,
            player_id=game.player_id,
            summary=draft.summary,
            key_moments=json.dumps(draft.key_moments),
            concepts=json.dumps([concept.value for concept in digest.concepts]),
            source=source,
        )
    )
    session.commit()
