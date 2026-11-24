"""Active tutor mode: analyse a proposed move, then coach it before it is locked in (spec 6.2).

The proposal response carries only the win probabilities. The hint ladder runs afterwards as a
background task and reaches the client as a `coach_comment` SSE event: a move with something
wrong gets level 1 (nudge), 2 (pointer) or 3 (consequence) by how often the player has already
rethought it. A fine move is silent, except for an occasional remark on a notable board feature.
Nothing here, nor anything sent to the model, names the best move: `active_pre_lock_in` makes the
prompt builder drop it and every fact that contains it.
"""

import asyncio
import json
import logging

import chess
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from chess_tutor.analysis.move_delta import MoveDelta, move_delta
from chess_tutor.api.events import CoachCommentEvent, GameEvents
from chess_tutor.coach.grounding import coach_reply_with_grounding
from chess_tutor.coach.llm import LanguageModel, record_llm_cost
from chess_tutor.coach.profile import build_coach_profile, stored_phrasings
from chess_tutor.coach.prompt import RECENT_MESSAGES_SHOWN, CoachContext, CoachEvent
from chess_tutor.concepts.tagger import tag_mistake
from chess_tutor.engines.stockfish import StockfishPool
from chess_tutor.facts.models import Fact, FactType
from chess_tutor.facts.packet import build_fact_packet
from chess_tutor.games.service import GameService
from chess_tutor.models import (
    CoachMessage,
    CoachMessageKind,
    CoachMessageSource,
    CoachRole,
    Game,
    GameMode,
    MoveProposal,
)
from chess_tutor.openings.phase import detect_phase
from chess_tutor.settings import Settings

logger = logging.getLogger(__name__)

MAX_HINT_LEVEL = 3
BOARD_FEATURE_FACTS = {
    FactType.KING_SAFETY,
    FactType.PAWN_STRUCTURE,
    FactType.DEVELOPMENT,
    FactType.ACTIVITY,
}


def hint_level(rethinks: int) -> int:
    """The first proposal gets a nudge, each rethink moves up one rung, the top rung repeats."""
    return min(rethinks + 1, MAX_HINT_LEVEL)


class HintCoach:
    def __init__(
        self,
        engine: Engine,
        service: GameService,
        model: LanguageModel | None,
        events: GameEvents,
        settings: Settings,
    ) -> None:
        self._engine = engine
        self._service = service
        self._model = model
        self._events = events
        self._settings = settings
        self._tasks: set[asyncio.Task[None]] = set()
        # shortcut: in memory, so a restart resets the count; persist it if remarks must be
        # rate limited across restarts.
        self._proposals_since_remark: dict[int, int] = {}

    def schedule(
        self, game_id: int, proposal: MoveProposal, board: chess.Board, delta: MoveDelta
    ) -> None:
        self._proposals_since_remark[game_id] = self._proposals_since_remark.get(game_id, 0) + 1
        task = asyncio.create_task(self._run(game_id, proposal, board, delta))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def wait_until_idle(self) -> None:
        await asyncio.gather(*self._tasks)

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _run(
        self, game_id: int, proposal: MoveProposal, board: chess.Board, delta: MoveDelta
    ) -> None:
        # A background task has no caller to tell: log it, and never let it touch the game.
        try:
            await self._coach(game_id, proposal, board, delta)
        except Exception:
            logger.exception("active hint failed for game %s ply %s", game_id, proposal.ply)

    async def _coach(
        self, game_id: int, proposal: MoveProposal, board: chess.Board, delta: MoveDelta
    ) -> None:
        if self._model is None or proposal.uci is None:
            return
        phase = detect_phase(board, self._settings.thresholds.phase)
        packet = build_fact_packet(board)
        fine = not delta.facts
        level: int | None = None
        concepts = []
        if fine:
            feature = self._remark_feature(game_id, packet.facts)
            if feature is None:
                return
            packet = packet.model_copy(update={"facts": [feature]})
        else:
            level = hint_level(proposal.rethinks)
            concepts = tag_mistake(delta, phase, board)
        with Session(self._engine) as session:
            player_id = session.get_one(Game, game_id).player_id
            profile = build_coach_profile(
                session, player_id, concepts, stored_phrasings(session, player_id)
            )
        context = CoachContext(
            mode=GameMode.ACTIVE,
            active_pre_lock_in=True,
            phase=phase,
            event=CoachEvent.COMMENT_ON_MOVE if fine else CoachEvent.HINT,
            move_san=delta.move_san,
            fact_packet=packet,
            move_delta_facts=delta.facts,
            best_move_san=delta.best_move_san,
            best_line=delta.best_line,
            hint_level=level,
            profile=profile,
            recent_coach_messages=self._recent_texts(game_id),
        )
        reply = await coach_reply_with_grounding(self._model, context)
        with Session(self._engine) as session:
            record_llm_cost(session, game_id, reply.cost)
        # Nothing awaits between this check and the publish, so a rethink or lock-in cannot slip in.
        if not self._service.is_current_proposal(
            proposal.id, proposal.ply, proposal.uci, proposal.rethinks
        ):
            return
        with Session(self._engine) as session:
            session.add(
                CoachMessage(
                    game_id=game_id,
                    ply=proposal.ply,
                    role=CoachRole.COACH,
                    kind=CoachMessageKind.COMMENT if fine else CoachMessageKind.HINT,
                    hint_level=level,
                    text=reply.text,
                    fact_ids=json.dumps(reply.fact_ids),
                    source=CoachMessageSource(reply.source.value),
                    concepts=json.dumps([concept.value for concept in concepts]),
                )
            )
            session.commit()
        self._events.publish(
            game_id,
            CoachCommentEvent(
                ply=proposal.ply,
                text=reply.text,
                classification="remark" if fine else "hint",
                fact_ids=reply.fact_ids,
                source=reply.source.value,
                hint_level=level,
            ),
        )

    def _remark_feature(self, game_id: int, facts: list[Fact]) -> Fact | None:
        """A board feature worth a remark, at most once per `active_remark_every_n_proposals`."""
        if self._proposals_since_remark[game_id] < self._settings.active_remark_every_n_proposals:
            return None
        feature = next((fact for fact in facts if fact.type in BOARD_FEATURE_FACTS), None)
        if feature is not None:
            self._proposals_since_remark[game_id] = 0
        return feature

    def _recent_texts(self, game_id: int) -> list[str]:
        with Session(self._engine) as session:
            texts = session.scalars(
                select(CoachMessage.text)
                .where(CoachMessage.game_id == game_id, CoachMessage.role == CoachRole.COACH)
                .order_by(CoachMessage.id.desc())
                .limit(RECENT_MESSAGES_SHOWN)
            ).all()
        return list(reversed(texts))


class ActiveTutor:
    def __init__(
        self, service: GameService, pool: StockfishPool, hints: HintCoach | None = None
    ) -> None:
        self._service = service
        self._pool = pool
        self._hints = hints

    async def propose(self, game_id: int, uci: str) -> MoveProposal:
        proposal = await self._service.propose_move(game_id, uci)
        try:
            board = self._service.get_board(game_id)
            delta = await move_delta(board, chess.Move.from_uci(uci), self._pool)
        except BaseException:  # incl. cancellation: a stuck proposal would answer every retry 409
            self._service.withdraw_proposal(proposal.id, uci)
            raise
        self._service.record_proposal_analysis(
            proposal.id, uci, delta.win_probability_before, delta.win_probability_after
        )
        proposal.win_probability_before = delta.win_probability_before
        proposal.win_probability_after = delta.win_probability_after
        if self._hints is not None:
            self._hints.schedule(game_id, proposal, board, delta)
        return proposal
