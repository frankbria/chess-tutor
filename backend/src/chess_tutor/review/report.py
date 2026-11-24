"""Post-game review: the moments worth revisiting, with the best move revealed for each."""

import asyncio
import json
from dataclasses import dataclass

import chess
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from chess_tutor.analysis.recording import classify_stored_move
from chess_tutor.engines.stockfish import Budget, StockfishPool
from chess_tutor.models import (
    CoachMessage,
    CoachMessageKind,
    CoachRole,
    ConceptEvent,
    ConceptSource,
    Game,
    GameSummary,
    Move,
    PlayerColor,
)
from chess_tutor.rating.performance import record_performance
from chess_tutor.settings import ClassificationThresholds

MAX_SWINGS = 4
MAX_HIGHLIGHTS = 2  # swings + highlights bound the deep analyses a review costs
SWING_CLASSIFICATIONS = ("inaccuracy", "mistake", "blunder")
HIGHLIGHT_CLASSIFICATIONS = ("great", "brilliant")


@dataclass(frozen=True)
class KeyMoment:
    ply: int
    move_number: int
    san: str
    uci: str
    fen_before: str
    classification: str
    win_probability_before: float | None
    win_probability_after: float | None
    best_move_uci: str
    best_move_san: str


@dataclass(frozen=True)
class ReasoningExchange:
    ply: int
    answer: str
    coach_reply: str | None


@dataclass(frozen=True)
class StoredSummary:
    summary: str
    key_moments: list[str]
    concepts: list[str]
    source: str


@dataclass(frozen=True)
class ConceptHit:
    ply: int
    concept: str
    source: ConceptSource


@dataclass(frozen=True)
class PlyWinProbability:
    ply: int
    win_probability: float  # the mover's, after the move


@dataclass(frozen=True)
class Review:
    accuracy: float | None
    performance_rating: float | None
    win_probabilities: list[PlyWinProbability]
    key_moments: list[KeyMoment]
    reasoning: list[ReasoningExchange]
    summary: StoredSummary | None
    concepts: list[ConceptHit]


def _loss(move: Move) -> float:
    assert move.eval_before is not None and move.eval_after is not None
    return move.eval_before - move.eval_after


def pick_key_moments(player_moves: list[Move]) -> list[Move]:
    """The player's biggest swings against them, plus Great/Brilliant moves, in play order."""
    swings = sorted(
        (
            move
            for move in player_moves
            if move.classification in SWING_CLASSIFICATIONS
            and move.eval_before is not None
            and move.eval_after is not None
        ),
        key=_loss,
        reverse=True,
    )[:MAX_SWINGS]
    highlights = [m for m in player_moves if m.classification in HIGHLIGHT_CLASSIFICATIONS]
    return sorted([*swings, *highlights[:MAX_HIGHLIGHTS]], key=lambda move: move.ply)


async def _reveal(move: Move, pool: StockfishPool) -> KeyMoment:
    board = chess.Board(move.fen_before)
    [best_line, *_] = await pool.analyse(board, Budget.DEEP)
    best = best_line.pv[0]
    return KeyMoment(
        ply=move.ply,
        move_number=(move.ply + 1) // 2,
        san=move.san,
        uci=move.uci,
        fen_before=move.fen_before,
        classification=move.classification or "",
        win_probability_before=move.eval_before,
        win_probability_after=move.eval_after,
        best_move_uci=best.uci(),
        best_move_san=board.san(best),
    )


def _reasoning(messages: list[CoachMessage]) -> list[ReasoningExchange]:
    exchanges = []
    for index, message in enumerate(messages):
        if message.role is not CoachRole.PLAYER or message.kind is not CoachMessageKind.ANSWER:
            continue
        reply = next(
            (
                later.text
                for later in messages[index + 1 :]
                if later.role is CoachRole.COACH and later.ply == message.ply
            ),
            None,
        )
        exchanges.append(ReasoningExchange(message.ply, message.text, reply))
    return exchanges


async def _score_unscored_moves(
    engine: Engine,
    pool: StockfishPool,
    game: Game,
    parity: int,
    thresholds: ClassificationThresholds,
) -> None:
    """Regular and Active games are not scored live; score them once, then reviews are instant."""
    with Session(engine) as session:
        unscored = session.scalars(
            select(Move.ply).where(Move.game_id == game.id, Move.classification.is_(None))
        ).all()
    scoring = [ply for ply in unscored if ply % 2 == parity]
    await asyncio.gather(
        *(classify_stored_move(engine, pool, game.id, ply, thresholds) for ply in scoring)
    )
    if scoring:
        with Session(engine) as session:
            record_performance(session, session.get_one(Game, game.id))
            session.commit()


async def build_review(
    engine: Engine, pool: StockfishPool, game: Game, thresholds: ClassificationThresholds
) -> Review:
    """`game` must be finished. Stored data is read first; only the key moments hit Stockfish."""
    parity = 1 if game.player_color is PlayerColor.WHITE else 0
    await _score_unscored_moves(engine, pool, game, parity, thresholds)
    with Session(engine) as session:
        moves = list(
            session.scalars(select(Move).where(Move.game_id == game.id).order_by(Move.ply))
        )
        scored_game = session.get_one(Game, game.id)  # accuracy may have just been recorded
        accuracy, performance = scored_game.accuracy, scored_game.performance_rating
        win_probabilities = [
            PlyWinProbability(move.ply, move.eval_after)
            for move in moves
            if move.eval_after is not None
        ]
        chosen = pick_key_moments([move for move in moves if move.ply % 2 == parity])
        for move in chosen:
            session.expunge(move)
        messages = list(
            session.scalars(
                select(CoachMessage)
                .where(CoachMessage.game_id == game.id)
                .order_by(CoachMessage.id)
            )
        )
        stored = session.scalars(
            select(GameSummary).where(GameSummary.game_id == game.id)
        ).one_or_none()  # absent when the summary was never written, e.g. no coach ran
        concepts = [
            ConceptHit(event.ply, event.concept, event.source)
            for event in session.scalars(
                select(ConceptEvent)
                .where(ConceptEvent.game_id == game.id)
                .order_by(ConceptEvent.ply, ConceptEvent.id)
            )
        ]
        reasoning = _reasoning(messages)
        summary = (
            None
            if stored is None
            else StoredSummary(
                stored.summary,
                json.loads(stored.key_moments),
                json.loads(stored.concepts),
                stored.source.value,
            )
        )
    key_moments = list(await asyncio.gather(*(_reveal(move, pool) for move in chosen)))
    return Review(
        accuracy,
        performance,
        win_probabilities,
        key_moments,
        reasoning,
        summary,
        concepts,
    )
