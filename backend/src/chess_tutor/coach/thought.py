"""Passive mode's "what was your thought?" question (spec 6.3): when to ask, and what is pending.

A question is a stored coach message at the ply of the move it is about. The player closes it with
an ANSWER or a SKIPPED message at the same ply, so the stored conversation alone says whether one
is still open. Taking the move back deletes the messages of its ply, question included.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from chess_tutor.analysis.classification import MoveClassification
from chess_tutor.games.service import GameError
from chess_tutor.models import CoachMessage, CoachMessageKind, CoachRole, PlayerColor

KEY_MOMENTS = {
    MoveClassification.MISTAKE,
    MoveClassification.BLUNDER,
    MoveClassification.GREAT,
    MoveClassification.BRILLIANT,
}


class NoPendingQuestionError(GameError):
    pass


class CoachUnavailableError(GameError):
    pass


class NoSuchPlyError(GameError):
    pass


def player_move_number(ply: int, color: PlayerColor) -> int:
    return (ply + 1) // 2 if color is PlayerColor.WHITE else ply // 2


def pending_question(session: Session, game_id: int) -> CoachMessage | None:
    question = session.scalars(
        select(CoachMessage)
        .where(CoachMessage.game_id == game_id, CoachMessage.kind == CoachMessageKind.QUESTION)
        .order_by(CoachMessage.id.desc())
        .limit(1)
    ).first()
    if question is None:
        return None
    closed = session.scalars(
        select(CoachMessage.id)
        .where(
            CoachMessage.game_id == game_id,
            CoachMessage.role == CoachRole.PLAYER,
            CoachMessage.ply == question.ply,
            CoachMessage.id > question.id,
        )
        .limit(1)
    ).first()
    return None if closed else question


def question_due(
    session: Session, game_id: int, ply: int, color: PlayerColor, every_n_moves: int
) -> bool:
    """A key moment, with no question open and at least `every_n_moves` moves since the last."""
    if every_n_moves == 0 or pending_question(session, game_id) is not None:
        return False
    last_ply = session.scalars(
        select(CoachMessage.ply)
        .where(CoachMessage.game_id == game_id, CoachMessage.kind == CoachMessageKind.QUESTION)
        .order_by(CoachMessage.id.desc())
        .limit(1)
    ).first()
    if last_ply is None:
        return True
    gap = player_move_number(ply, color) - player_move_number(last_ply, color)
    return gap >= every_n_moves
