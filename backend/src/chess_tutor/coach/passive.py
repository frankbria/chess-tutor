"""Passive tutor mode: comment on the player's notable moves while the game goes on.

The opponent's reply never waits for this: `schedule` runs the analysis and the coach call as a
background task, and the comment reaches the client as a `coach_comment` SSE event whenever it is
ready (usually after the `opponent_move` event). Ordinary Good/Best moves cost no LLM call.
"""

import asyncio
import json
import logging

import chess
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from chess_tutor.analysis.classification import MoveClassification
from chess_tutor.analysis.move_delta import move_delta
from chess_tutor.analysis.recording import classify_stored_move
from chess_tutor.api.events import CoachCommentEvent, GameEvents
from chess_tutor.coach.grounding import coach_reply_with_grounding
from chess_tutor.coach.llm import LanguageModel, record_llm_cost
from chess_tutor.coach.profile import build_coach_profile, stored_phrasings
from chess_tutor.coach.prompt import RECENT_MESSAGES_SHOWN, CoachContext, CoachEvent
from chess_tutor.coach.summary import build_digest, store_summary, summarize_game
from chess_tutor.concepts.state import refresh_concept_states
from chess_tutor.concepts.tagger import record_concepts, tag_mistake
from chess_tutor.engines.stockfish import StockfishPool
from chess_tutor.facts.packet import build_fact_packet
from chess_tutor.models import (
    CoachMessage,
    CoachMessageKind,
    CoachMessageSource,
    CoachRole,
    Game,
    GameMode,
    GameStatus,
    GameSummary,
    PlayerColor,
)
from chess_tutor.openings.phase import detect_phase
from chess_tutor.rating.performance import record_performance
from chess_tutor.settings import Settings

logger = logging.getLogger(__name__)

COMMENTED = {
    MoveClassification.INACCURACY,
    MoveClassification.MISTAKE,
    MoveClassification.BLUNDER,
    MoveClassification.GREAT,
    MoveClassification.BRILLIANT,
}
MISTAKES = {MoveClassification.MISTAKE, MoveClassification.BLUNDER}


def player_move_number(ply: int, color: PlayerColor) -> int:
    return (ply + 1) // 2 if color is PlayerColor.WHITE else ply // 2


# The SDK's own timeout is minutes long; a game-ending request should not hang that long.
FINAL_COMMENT_WAIT_SECONDS = 30.0


class PassiveCoach:
    def __init__(
        self,
        engine: Engine,
        pool: StockfishPool,
        model: LanguageModel | None,
        events: GameEvents,
        settings: Settings,
    ) -> None:
        self._engine = engine
        self._pool = pool
        self._model = model
        self._events = events
        self._settings = settings
        self._tasks: dict[asyncio.Task[None], int] = {}  # task -> game id
        # Per game, so one stalled LLM call cannot hold up another game; within a game moves are
        # coached in order, which keeps the rate limit and the recent-messages context exact.
        self._in_order: dict[int, asyncio.Lock] = {}
        # Counted at schedule and released in `_run`'s finally, so whichever task takes it to zero
        # is certain to be the last (task registry removal happens later, in a done callback).
        self._pending: dict[int, int] = {}
        # Apart from `_tasks` so a request waiting for the final comment never waits for these.
        self._summary_tasks: set[asyncio.Task[None]] = set()

    def player_moved(self, game: Game, ply: int) -> None:
        if game.mode is GameMode.PASSIVE:
            self.schedule(game.id, ply)

    def is_coaching(self, game_id: int) -> bool:
        return self._pending.get(game_id, 0) > 0

    def schedule(self, game_id: int, ply: int) -> None:
        task = asyncio.create_task(self._run(game_id, ply))
        self._tasks[task] = game_id
        self._pending[game_id] = self._pending.get(game_id, 0) + 1
        task.add_done_callback(lambda done: self._tasks.pop(done, None))

    def game_finished(self, game_id: int) -> None:
        """A game that is still being coached is summarised by its last coach task instead."""
        if not self.is_coaching(game_id):
            self._schedule_summary(game_id)

    def _schedule_summary(self, game_id: int) -> None:
        task = asyncio.create_task(self._summarize(game_id))
        self._summary_tasks.add(task)
        task.add_done_callback(self._summary_tasks.discard)

    async def wait_until_idle(self) -> None:
        await asyncio.gather(*self._tasks)
        await asyncio.gather(*self._summary_tasks)

    async def wait_for_game(self, game_id: int) -> None:
        tasks = [task for task, game in self._tasks.items() if game == game_id]
        if tasks:
            await asyncio.wait(tasks, timeout=FINAL_COMMENT_WAIT_SECONDS)

    async def close(self) -> None:
        tasks = [*self._tasks, *self._summary_tasks]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(self, game_id: int, ply: int) -> None:
        try:
            async with self._in_order.setdefault(game_id, asyncio.Lock()):
                await self.coach_move(game_id, ply)
        # A background task has no caller to tell: log it, and never let it touch the game.
        except Exception:
            logger.exception("passive coaching failed for game %s ply %s", game_id, ply)
        finally:
            self._finish_game_if_last(game_id)

    def _finish_game_if_last(self, game_id: int) -> None:
        """A game that ended while its moves were being coached gets its concept states now."""
        self._pending[game_id] -= 1
        if self._pending[game_id] > 0:
            return
        del self._pending[game_id]
        self._in_order.pop(game_id, None)
        with Session(self._engine) as session:
            game = session.get_one(Game, game_id)
            if game.status is GameStatus.FINISHED and game.ended_at is not None:
                refresh_concept_states(
                    session,
                    game.player_id,
                    game_id,
                    game.ended_at,
                    self._settings.thresholds.concept_state,
                )
                session.commit()
                self._schedule_summary(game_id)

    async def _summarize(self, game_id: int) -> None:
        """Store the game's summary once; a failure here is logged and never touches the game."""
        try:
            with Session(self._engine) as session:
                game = session.get_one(Game, game_id)
                if session.scalar(select(GameSummary.id).where(GameSummary.game_id == game_id)):
                    return
                digest = build_digest(session, game)
            draft, source, usages = await summarize_game(self._model, digest)
            with Session(self._engine) as session:
                store_summary(session, session.get_one(Game, game_id), digest, draft, source)
                record_llm_cost(session, game_id, sum(usage.cost for usage in usages))
        except Exception:
            logger.exception("summarising game %s failed", game_id)

    async def coach_move(self, game_id: int, ply: int) -> None:
        """Classify the player's move at `ply`, tag its concepts, and comment if it is notable."""
        stored = await classify_stored_move(
            self._engine, self._pool, game_id, ply, self._settings.thresholds.classification
        )
        assert stored.classification is not None
        classification = MoveClassification(stored.classification)
        with Session(self._engine) as session:
            game = session.get_one(Game, game_id)
            if game.status is GameStatus.FINISHED:  # finishing ran before this move was scored
                record_performance(session, game)
                session.commit()
        if classification not in COMMENTED:
            return
        board = chess.Board(stored.fen_before)
        move = chess.Move.from_uci(stored.uci)
        delta = await move_delta(board, move, self._pool)
        phase = detect_phase(board, self._settings.thresholds.phase)
        concepts = tag_mistake(delta, phase, board) if classification in MISTAKES else []
        with Session(self._engine) as session:
            player_id = session.get_one(Game, game_id).player_id
            record_concepts(session, player_id, game_id, ply, concepts)
            session.commit()
        if self._model is None or not self._due(game_id, ply):
            return
        with Session(self._engine) as session:
            profile = build_coach_profile(
                session, player_id, concepts, stored_phrasings(session, player_id)
            )
        context = CoachContext(
            mode=GameMode.PASSIVE,
            active_pre_lock_in=False,
            phase=phase,
            event=CoachEvent.COMMENT_ON_MOVE,
            move_san=delta.move_san,
            fact_packet=build_fact_packet(board),
            move_delta_facts=delta.facts,
            best_move_san=delta.best_move_san,
            best_line=delta.best_line,
            profile=profile,
            recent_coach_messages=self._recent_texts(game_id),
        )
        reply = await coach_reply_with_grounding(self._model, context)
        with Session(self._engine) as session:
            session.add(
                CoachMessage(
                    game_id=game_id,
                    ply=ply,
                    role=CoachRole.COACH,
                    kind=CoachMessageKind.COMMENT,
                    text=reply.text,
                    fact_ids=json.dumps(reply.fact_ids),
                    source=CoachMessageSource(reply.source.value),
                    concepts=json.dumps([concept.value for concept in concepts]),
                )
            )
            session.commit()
            record_llm_cost(session, game_id, reply.cost)
        self._events.publish(
            game_id,
            CoachCommentEvent(
                ply=ply,
                text=reply.text,
                classification=classification.value,
                fact_ids=reply.fact_ids,
                source=reply.source.value,
            ),
        )

    def _due(self, game_id: int, ply: int) -> bool:
        """At most one comment per move, and one per `coach_comment_every_n_moves` player moves."""
        with Session(self._engine) as session:
            color = session.get_one(Game, game_id).player_color
            last_ply = session.scalars(
                select(CoachMessage.ply)
                .where(
                    CoachMessage.game_id == game_id,
                    CoachMessage.kind == CoachMessageKind.COMMENT,
                )
                .order_by(CoachMessage.ply.desc())
                .limit(1)
            ).first()
        if last_ply is None:
            return True
        gap = player_move_number(ply, color) - player_move_number(last_ply, color)
        return gap >= self._settings.coach_comment_every_n_moves

    def _recent_texts(self, game_id: int) -> list[str]:
        with Session(self._engine) as session:
            texts = session.scalars(
                select(CoachMessage.text)
                .where(CoachMessage.game_id == game_id, CoachMessage.role == CoachRole.COACH)
                .order_by(CoachMessage.id.desc())
                .limit(RECENT_MESSAGES_SHOWN)
            ).all()
        return list(reversed(texts))
