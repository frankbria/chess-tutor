"""Opponent steering (spec section 14, stretch): on a fraction of moves, play one of Maia's top
human-likely moves whose resulting position holds a motif the player is working on, if it costs
at most a small Stockfish eval loss. All other moves are the wrapped engine's own, so Maia's
normal sampling (tail mistakes included) and hence the opponent's strength is preserved."""

import logging
import random
from collections.abc import Callable
from types import MappingProxyType
from typing import Protocol, Self

import chess
import chess.engine
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from chess_tutor.concepts.taxonomy import Concept
from chess_tutor.engines.human_moves import HumanMovePrediction
from chess_tutor.engines.opponent import OpponentEngine
from chess_tutor.engines.stockfish import AnalysedLine, Budget
from chess_tutor.facts.models import FactType
from chess_tutor.facts.packet import build_fact_packet
from chess_tutor.models import ConceptState, ConceptStatus, Player

logger = logging.getLogger(__name__)

# Fact types whose presence in the position the player must answer makes it test the concept.
CONCEPT_MOTIFS = MappingProxyType(
    {
        Concept.HANGING_PIECE: frozenset({FactType.HANGING_PIECE}),
        Concept.MISSED_FORK: frozenset({FactType.FORK}),
        Concept.ALLOWED_FORK: frozenset({FactType.FORK}),
        Concept.PIN_BLINDNESS: frozenset({FactType.PIN}),
        Concept.SKEWER_BLINDNESS: frozenset({FactType.SKEWER}),
        Concept.DISCOVERED_ATTACK_BLINDNESS: frozenset({FactType.DISCOVERED_ATTACK}),
        Concept.BACK_RANK: frozenset({FactType.BACK_RANK_WEAKNESS}),
        Concept.MISSED_MATE: frozenset({FactType.MATE_IN_ONE}),
        Concept.ALLOWED_MATE: frozenset({FactType.MATE_THREAT}),
        Concept.OVERLOADED_DEFENDER: frozenset({FactType.OVERLOADED_DEFENDER}),
        Concept.TRAPPED_PIECE: frozenset({FactType.TRAPPED_PIECE}),
    }
)

MATE_SCORE_CENTIPAWNS = 10_000


class HumanMoveSource(Protocol):
    async def predict_human_moves(
        self, board: chess.Board, elo: int, k: int
    ) -> list[HumanMovePrediction]: ...


class PositionAnalyser(Protocol):
    async def analyse(
        self, board: chess.Board, budget: Budget | chess.engine.Limit, multipv: int = 1
    ) -> list[AnalysedLine]: ...


def focus_concepts_of_first_player(database: Engine) -> frozenset[Concept]:
    """One player in v1: the earliest player's FOCUS concepts still in the taxonomy."""
    with Session(database) as session:
        player_id = session.scalars(select(Player.id).order_by(Player.id).limit(1)).first()
        names = session.scalars(
            select(ConceptState.concept).where(
                ConceptState.player_id == player_id, ConceptState.state == ConceptStatus.FOCUS
            )
        ).all()
    return frozenset(Concept(name) for name in names if name in Concept)


class SteeringOpponent:
    def __init__(
        self,
        wrapped: OpponentEngine,
        predictor: HumanMoveSource,
        analyser: PositionAnalyser,
        focus_concepts: Callable[[], frozenset[Concept]],
        rate: float = 0.25,
        max_eval_loss_centipawns: int = 30,
        top_moves: int = 4,
        probability_floor: float = 0.05,
        random_source: random.Random | None = None,
    ) -> None:
        self._wrapped = wrapped
        self._predictor = predictor
        self._analyser = analyser
        self._focus_concepts = focus_concepts
        self._rate = rate
        self._max_eval_loss = max_eval_loss_centipawns
        self._top_moves = top_moves
        self._probability_floor = probability_floor
        self._random = random_source or random.Random()

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def start(self) -> None:
        await self._wrapped.start()

    async def close(self) -> None:
        await self._wrapped.close()

    async def choose_move(self, board: chess.Board, elo: int) -> chess.Move:
        if (
            self._random.random() >= self._rate
        ):  # most moves stay Maia's own, tail blunders included
            return await self._wrapped.choose_move(board, elo)
        motifs = {
            fact_type
            for concept in self._focus_concepts()
            for fact_type in CONCEPT_MOTIFS.get(concept, frozenset())
        }
        if motifs:
            try:
                steered = await self._steer(board, elo, motifs)
            except Exception:  # steering is a bonus: any failure means the normal opponent
                logger.warning("opponent steering failed; using the wrapped engine", exc_info=True)
                steered = None
            if steered is not None:
                return steered
        return await self._wrapped.choose_move(board, elo)

    async def _steer(
        self, board: chess.Board, elo: int, motifs: set[FactType]
    ) -> chess.Move | None:
        candidates = [
            candidate
            for candidate in await self._predictor.predict_human_moves(board, elo, self._top_moves)
            if candidate.probability >= self._probability_floor
        ]
        if len(candidates) < 2:
            return None
        already_present = {fact.id for fact in build_fact_packet(board).facts}
        tests_focus = [
            self._creates_motif(board, candidate.move, motifs, already_present)
            for candidate in candidates
        ]
        if not any(tests_focus):
            return None
        evaluations = [await self._evaluation(board, candidate.move) for candidate in candidates]
        # the cost is measured against Maia's own first choice
        affordable = [
            index
            for index, evaluation in enumerate(evaluations)
            if evaluations[0] - evaluation <= self._max_eval_loss
        ]
        if not any(tests_focus[index] for index in affordable):
            return None
        motif_moves = [index for index in affordable if tests_focus[index]]
        weights = [candidates[index].probability for index in motif_moves]
        chosen = self._random.choices(motif_moves, weights)[0]
        return candidates[chosen].move

    @staticmethod
    def _creates_motif(
        board: chess.Board, move: chess.Move, motifs: set[FactType], already_present: set[str]
    ) -> bool:
        """A motif the move newly brings about; one already on the board steers nothing."""
        after = board.copy(stack=False)
        after.push(move)
        return any(
            fact.type in motifs and fact.id not in already_present
            for fact in build_fact_packet(after).facts
        )

    async def _evaluation(self, board: chess.Board, move: chess.Move) -> int:
        """Centipawns for the side that just moved."""
        after = board.copy(stack=False)
        after.push(move)
        if after.is_checkmate():
            return MATE_SCORE_CENTIPAWNS
        if after.is_game_over():
            return 0
        (line,) = await self._analyser.analyse(after, Budget.QUICK)
        return -line.side_to_move_score.score(mate_score=MATE_SCORE_CENTIPAWNS)
