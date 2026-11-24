"""Passive tutor mode: comment on the player's notable moves while the game goes on.

The opponent's reply never waits for this: `schedule` runs the analysis and the coach call as a
background task, and the comment reaches the client as a `coach_comment` SSE event whenever it is
ready (usually after the `opponent_move` event). Ordinary Good/Best moves cost no LLM call.
"""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from uuid import uuid4

import chess
from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError, NoResultFound
from sqlalchemy.orm import Session

from chess_tutor.analysis.classification import MoveClassification, analyse_position
from chess_tutor.analysis.move_delta import MoveDelta, move_delta, san_line
from chess_tutor.analysis.recording import classify_stored_move
from chess_tutor.api.events import CoachCommentEvent, GameEvents
from chess_tutor.api.settings import load_preferences
from chess_tutor.coach.active import hint_level
from chess_tutor.coach.grounding import THOUGHT_QUESTION, GroundedReply, coach_reply_with_grounding
from chess_tutor.coach.latency import reply_with_instant_template
from chess_tutor.coach.llm import LanguageModel, record_llm_cost
from chess_tutor.coach.profile import build_coach_profile, stored_phrasings
from chess_tutor.coach.prompt import RECENT_MESSAGES_SHOWN, CoachContext, CoachEvent, CoachReply
from chess_tutor.coach.reasoning import tag_reasoning
from chess_tutor.coach.summary import build_digest, store_summary, summarize_game
from chess_tutor.coach.thought import (
    KEY_MOMENTS,
    CoachUnavailableError,
    NoPendingQuestionError,
    NoSuchPlyError,
    pending_question,
    player_move_number,
    question_due,
)
from chess_tutor.concepts.state import refresh_concept_states
from chess_tutor.concepts.tagger import record_concepts, tag_mistake
from chess_tutor.concepts.taxonomy import Concept
from chess_tutor.engines.stockfish import Budget, StockfishPool
from chess_tutor.facts.packet import build_fact_packet
from chess_tutor.games.service import GameNotActiveError, WrongModeError
from chess_tutor.models import (
    CoachMessage,
    CoachMessageKind,
    CoachMessageSource,
    CoachRole,
    ConceptEvent,
    Game,
    GameMode,
    GameStatus,
    GameSummary,
    Move,
    MoveProposal,
)
from chess_tutor.openings.phase import GamePhase, detect_phase
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
        # Apart from `_tasks` so a request waiting for the final comment never waits for these;
        # holds summaries and reasoning tagging, and is cancelled on close.
        self._summary_tasks: set[asyncio.Task[None]] = set()

    def player_moved(self, game: Game, ply: int) -> None:
        if game.mode is GameMode.PASSIVE:
            self.schedule(game.id, ply)

    def is_coaching(self, game_id: int) -> bool:
        return self._pending.get(game_id, 0) > 0

    def schedule(self, game_id: int, ply: int, *, skipped_question: bool = False) -> None:
        self._spawn(
            game_id,
            lambda: self.coach_move(game_id, ply, skipped_question=skipped_question),
            f"coaching ply {ply}",
        )

    def handle_message(self, game_id: int, text: str) -> None:
        """An open question takes the text as its answer; otherwise it is a question to ask."""
        with Session(self._engine) as session:
            game = session.get_one(Game, game_id)
            if game.status is GameStatus.FINISHED:
                raise GameNotActiveError("the game is over")
            if game.mode is GameMode.REGULAR:
                raise WrongModeError("the coach only answers in tutor games")
            answering = pending_question(session, game_id) is not None
        if answering:
            self.answer_question(game_id, text)
        else:
            self._ask(game_id, text)

    def answer_question(self, game_id: int, text: str) -> None:
        """Store the answer to the open question now; the coach replies in the background."""
        ply = self._close_question(game_id, CoachMessageKind.ANSWER, text)
        self._spawn(game_id, lambda: self.reply_to_answer(game_id, ply, text), "answering")
        self._schedule_tagging(game_id, ply, text)

    def skip_question(self, game_id: int) -> None:
        """A skipped question still owes the player the normal comment on that move."""
        ply = self._close_question(game_id, CoachMessageKind.SKIPPED, "")
        self.schedule(game_id, ply, skipped_question=True)

    def _ask(self, game_id: int, text: str) -> None:
        """Store the player's question at the last played ply (0 before any move), the position it
        is about, so the review shows it under that move; the reply is background."""
        with Session(self._engine) as session:
            ply = len(_board_after(session, game_id).move_stack)
            self._store_message(
                session, game_id, ply, CoachRole.PLAYER, CoachMessageKind.ASKED, text
            )
            session.commit()
        self._spawn(game_id, lambda: self.reply_to_question(game_id, ply, text), "asked")

    def _close_question(self, game_id: int, kind: CoachMessageKind, text: str) -> int:
        with Session(self._engine) as session:
            ply = self._open_question(session, game_id).ply
            self._store_message(session, game_id, ply, CoachRole.PLAYER, kind, text)
            session.commit()
        return ply

    def _store_message(
        self,
        session: Session,
        game_id: int,
        ply: int,
        role: CoachRole,
        kind: CoachMessageKind,
        text: str,
        reply: GroundedReply | None = None,
    ) -> None:
        session.add(
            CoachMessage(
                game_id=game_id,
                ply=ply,
                role=role,
                kind=kind,
                text=text if reply is None else reply.text,
                fact_ids="[]" if reply is None else json.dumps(reply.fact_ids),
                source=(
                    CoachMessageSource.TEMPLATE
                    if reply is None
                    else CoachMessageSource(reply.source.value)
                ),
                concepts="[]",
            )
        )

    def _open_question(self, session: Session, game_id: int) -> CoachMessage:
        if session.get_one(Game, game_id).status is GameStatus.FINISHED:
            raise GameNotActiveError("the game is over")
        question = pending_question(session, game_id)
        if question is None:
            raise NoPendingQuestionError("the coach has not asked a question")
        return question

    def _spawn(self, game_id: int, work: Callable[[], Awaitable[None]], description: str) -> None:
        task = asyncio.create_task(self._run(game_id, work, description))
        self._tasks[task] = game_id
        self._pending[game_id] = self._pending.get(game_id, 0) + 1
        task.add_done_callback(lambda done: self._tasks.pop(done, None))

    def game_finished(self, game_id: int) -> None:
        """A game that is still being coached is summarised by its last coach task instead."""
        if not self.is_coaching(game_id):
            self._schedule_summary(game_id)

    def _schedule_tagging(self, game_id: int, ply: int, text: str) -> None:
        """Outside the per-game lock and pending count, so a slow call delays no comment."""
        task = asyncio.create_task(self._tag_reasoning(game_id, ply, text))
        self._summary_tasks.add(task)
        task.add_done_callback(self._summary_tasks.discard)

    async def _tag_reasoning(self, game_id: int, ply: int, text: str) -> None:
        try:
            await tag_reasoning(self._engine, self._model, game_id, ply, text)
        except Exception:
            logger.exception("tagging the reasoning of game %s ply %s failed", game_id, ply)

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

    async def _run(
        self, game_id: int, work: Callable[[], Awaitable[None]], description: str
    ) -> None:
        try:
            async with self._in_order.setdefault(game_id, asyncio.Lock()):
                await work()
        # A background task has no caller to tell: log it, and never let it touch the game.
        except Exception:
            logger.exception("passive coaching failed for game %s (%s)", game_id, description)
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
                if game.status is not GameStatus.FINISHED or game.result is None:
                    return  # a summary for a running game would block the real one (unique game)
                if session.scalar(select(GameSummary.id).where(GameSummary.game_id == game_id)):
                    return
                digest = build_digest(session, game)
            draft, source, usages = await summarize_game(self._model, digest)
            with Session(self._engine) as session:
                record_llm_cost(session, game_id, sum(usage.cost for usage in usages))
                try:
                    store_summary(session, session.get_one(Game, game_id), digest, draft, source)
                except IntegrityError:  # a concurrent call stored it first; its cost still counts
                    session.rollback()
        except Exception:
            logger.exception("summarising game %s failed", game_id)

    async def coach_move(self, game_id: int, ply: int, *, skipped_question: bool = False) -> None:
        """Classify the player's move at `ply`, tag its concepts, and comment if it is notable.

        A skipped question's comment is owed to the player, so it ignores the comment spacing.
        """
        started = time.monotonic()
        try:
            stored = await classify_stored_move(
                self._engine, self._pool, game_id, ply, self._settings.thresholds.classification
            )
        except NoResultFound:  # the move was taken back before or while it was scored
            return
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
            if not self._still_played(session, stored):
                return
            game = session.get_one(Game, game_id)
            already_recorded = session.scalars(
                select(ConceptEvent.id).where(
                    ConceptEvent.game_id == game_id, ConceptEvent.ply == ply
                )
            ).first()
            if not already_recorded:  # a skipped question comes back through this path
                record_concepts(session, game.player_id, game_id, ply, concepts)
            session.commit()
            ask = (
                self._model is not None
                and game.status is GameStatus.ACTIVE
                and classification in KEY_MOMENTS
                and question_due(
                    session,
                    game_id,
                    ply,
                    game.player_color,
                    self._settings.thought_question_every_n_moves,
                )
            )
        if ask:
            self._ask_thought(game_id, stored, classification, phase, delta.move_san)
            return
        if self._model is None or not (skipped_question or self._due(game_id, ply)):
            return
        context = self._context(
            game_id, board, delta, phase, concepts, CoachEvent.COMMENT_ON_MOVE, None
        )
        await self._reply_and_store(
            game_id,
            stored,
            context,
            CoachMessageKind.COMMENT,
            classification,
            phase,
            concepts,
            started,
        )

    async def reply_to_answer(self, game_id: int, ply: int, answer: str) -> None:
        """The coach's grounded response to the player's reasoning, stored at the same ply."""
        started = time.monotonic()
        if self._model is None:
            return
        with Session(self._engine) as session:
            stored = session.scalars(
                select(Move).where(Move.game_id == game_id, Move.ply == ply)
            ).one_or_none()
            if stored is None or stored.classification is None:  # taken back since the answer
                return
            session.expunge(stored)
        board = chess.Board(stored.fen_before)
        delta = await move_delta(board, chess.Move.from_uci(stored.uci), self._pool)
        phase = detect_phase(board, self._settings.thresholds.phase)
        classification = MoveClassification(stored.classification)
        concepts = tag_mistake(delta, phase, board) if classification in MISTAKES else []
        context = self._context(
            game_id, board, delta, phase, concepts, CoachEvent.ANSWER_QUESTION, answer
        )
        await self._reply_and_store(
            game_id,
            stored,
            context,
            CoachMessageKind.ANSWER,
            classification,
            phase,
            concepts,
            started,
        )

    async def reply_to_question(self, game_id: int, ply: int, text: str) -> None:
        """The coach's grounded answer about the current position, published as it is stored."""
        if self._model is None:
            return
        with Session(self._engine) as session:
            game = session.get_one(Game, game_id)
            board = _board_after(session, game_id)
            if len(board.move_stack) != ply:  # a move or takeback since the question
                return
            asked_fen = board.fen()
            mode = game.mode
            proposal = (
                _pending_proposal(session, game_id, ply + 1) if mode is GameMode.ACTIVE else None
            )
            asked_proposal = _proposal_key(proposal)
        context = await self._question_context(game_id, board, text, mode, proposal)
        reply = await coach_reply_with_grounding(self._model, context)
        with Session(self._engine) as session:
            record_llm_cost(session, game_id, reply.cost)  # billed even when the answer is stale
            overtaken = _board_after(session, game_id).fen() != asked_fen or asked_proposal != (
                _proposal_key(_pending_proposal(session, game_id, ply + 1))
            )
            if overtaken:  # a takeback, a new line, or a rethought proposal
                return
            self._store_message(
                session, game_id, ply, CoachRole.COACH, CoachMessageKind.ANSWER, "", reply
            )
            session.commit()
        self._events.publish(
            game_id,
            CoachCommentEvent(
                ply=ply,
                text=reply.text,
                classification="",
                phase=context.phase,
                fact_ids=reply.fact_ids,
                source=reply.source.value,
                kind=CoachMessageKind.ANSWER,
            ),
        )

    async def ask_about_position(self, game_id: int, ply: int, text: str) -> GroundedReply:
        """Review: a question about the position after `ply` of a finished game. Best moves may be
        named, so nothing here is pre-lock-in."""
        if self._model is None:
            raise CoachUnavailableError("the coach is not running")
        with Session(self._engine) as session:
            game = session.get_one(Game, game_id)
            board = _board_after(session, game_id, ply)
            if len(board.move_stack) != ply:
                raise NoSuchPlyError(f"the game has no ply {ply}")
            mode = game.mode
        context = await self._question_context(game_id, board, text, mode, None, review=True)
        reply = await coach_reply_with_grounding(self._model, context)
        with Session(self._engine) as session:
            self._store_message(
                session, game_id, ply, CoachRole.PLAYER, CoachMessageKind.ASKED, text
            )
            self._store_message(
                session, game_id, ply, CoachRole.COACH, CoachMessageKind.ANSWER, "", reply
            )
            session.commit()
            record_llm_cost(session, game_id, reply.cost)
        return reply

    async def _question_context(
        self,
        game_id: int,
        board: chess.Board,
        text: str,
        mode: GameMode,
        proposal: MoveProposal | None,
        review: bool = False,
    ) -> CoachContext:
        """A fresh packet for `board`. A pending Active proposal brings its move's facts too, and
        until it is locked in (never in a review) the best move is withheld and the ladder applies.
        """
        pre_lock_in = mode is GameMode.ACTIVE and not review
        phase = detect_phase(board, self._settings.thresholds.phase)
        if proposal is not None and proposal.uci is not None:
            delta = await move_delta(board, chess.Move.from_uci(proposal.uci), self._pool)
            move_san, move_facts = delta.move_san, delta.facts
            best_move_san, best_line = delta.best_move_san, delta.best_line
        else:
            [best, *_] = await analyse_position(self._pool, board, Budget.STANDARD)
            best_line = san_line(board, best.pv)
            move_san, move_facts, best_move_san = "", [], best_line[0] if best_line else None
        with Session(self._engine) as session:
            player_id = session.get_one(Game, game_id).player_id
            verbosity = load_preferences(session, self._settings.maia_model).coach_verbosity
            profile = build_coach_profile(
                session, player_id, [], stored_phrasings(session, player_id)
            )
        rethinks = proposal.rethinks if proposal is not None else 0
        return CoachContext(
            mode=mode,
            active_pre_lock_in=pre_lock_in,
            phase=phase,
            event=CoachEvent.ANSWER_QUESTION,
            move_san=move_san,
            fact_packet=build_fact_packet(board),
            move_delta_facts=move_facts,
            best_move_san=best_move_san,
            best_line=best_line,
            hint_level=hint_level(rethinks) if pre_lock_in else None,
            profile=profile,
            recent_coach_messages=self._recent_texts(game_id),
            player_message=text,
            verbosity=verbosity,
        )

    def _ask_thought(
        self,
        game_id: int,
        stored: Move,
        classification: MoveClassification,
        phase: GamePhase,
        move_san: str,
    ) -> None:
        text = THOUGHT_QUESTION.format(move=move_san)
        with Session(self._engine) as session:
            if not self._still_played(session, stored):
                return
            session.add(
                CoachMessage(
                    game_id=game_id,
                    ply=stored.ply,
                    role=CoachRole.COACH,
                    kind=CoachMessageKind.QUESTION,
                    text=text,
                    fact_ids="[]",
                    source=CoachMessageSource.TEMPLATE,
                    concepts="[]",
                )
            )
            session.commit()
        self._events.publish(
            game_id,
            CoachCommentEvent(
                ply=stored.ply,
                text=text,
                classification=classification.value,
                phase=phase,
                fact_ids=[],
                source=CoachMessageSource.TEMPLATE.value,
                kind=CoachMessageKind.QUESTION,
            ),
        )

    def _context(
        self,
        game_id: int,
        board: chess.Board,
        delta: MoveDelta,
        phase: GamePhase,
        concepts: list[Concept],
        event: CoachEvent,
        player_message: str | None,
    ) -> CoachContext:
        with Session(self._engine) as session:
            player_id = session.get_one(Game, game_id).player_id
            verbosity = load_preferences(session, self._settings.maia_model).coach_verbosity
            profile = build_coach_profile(
                session, player_id, concepts, stored_phrasings(session, player_id)
            )
        return CoachContext(
            mode=GameMode.PASSIVE,
            active_pre_lock_in=False,
            phase=phase,
            event=event,
            move_san=delta.move_san,
            fact_packet=build_fact_packet(board),
            move_delta_facts=delta.facts,
            best_move_san=delta.best_move_san,
            best_line=delta.best_line,
            profile=profile,
            recent_coach_messages=self._recent_texts(game_id),
            player_message=player_message,
            verbosity=verbosity,
        )

    async def _reply_and_store(
        self,
        game_id: int,
        stored: Move,
        context: CoachContext,
        kind: CoachMessageKind,
        classification: MoveClassification,
        phase: GamePhase,
        concepts: list[Concept],
        started: float,
    ) -> None:
        assert self._model is not None
        message_key = uuid4().hex

        def template_event(template: CoachReply) -> CoachCommentEvent:
            return CoachCommentEvent(
                ply=stored.ply,
                text=template.text,
                classification=classification.value,
                phase=phase,
                fact_ids=template.fact_ids,
                source=CoachMessageSource.TEMPLATE.value,
                kind=kind,
                provisional=True,
                message_key=message_key,
            )

        def settle(reply: GroundedReply) -> bool:
            with Session(self._engine) as session:
                if not self._still_played(session, stored):  # taken back while the LLM replied
                    return False
                session.add(
                    CoachMessage(
                        game_id=game_id,
                        ply=stored.ply,
                        role=CoachRole.COACH,
                        kind=kind,
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
                    ply=stored.ply,
                    text=reply.text,
                    classification=classification.value,
                    phase=phase,
                    fact_ids=reply.fact_ids,
                    source=reply.source.value,
                    kind=kind,
                    message_key=message_key,
                ),
            )
            return True

        await reply_with_instant_template(
            self._model, context, started, self._events, game_id, template_event, settle
        )

    def _still_played(self, session: Session, scored: Move) -> bool:
        """A taken-back move's row is deleted, so a late result for it must not be stored.

        SQLite reuses freed row ids, so the id alone is not enough: the row must still hold the
        same move at the same ply.
        """
        current = session.get(Move, scored.id)
        return current is not None and (current.ply, current.uci) == (scored.ply, scored.uci)

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


def _board_after(session: Session, game_id: int, ply: int | None = None) -> chess.Board:
    """The stored moves replayed, up to and including `ply` when given."""
    query = select(Move.uci).where(Move.game_id == game_id).order_by(Move.ply)
    if ply is not None:
        query = query.where(Move.ply <= ply)
    board = chess.Board()
    for uci in session.scalars(query):
        board.push_uci(uci)
    return board


def _proposal_key(proposal: MoveProposal | None) -> tuple[str | None, int] | None:
    return None if proposal is None else (proposal.uci, proposal.rethinks)


def _pending_proposal(session: Session, game_id: int, ply: int) -> MoveProposal | None:
    return session.scalars(
        select(MoveProposal).where(
            MoveProposal.game_id == game_id,
            MoveProposal.ply == ply,
        )
    ).first()
