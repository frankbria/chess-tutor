"""Real Maia-3 and real Stockfish: does steering raise the share of focus-motif positions, and
what does it cost in move quality? Counts are printed (run with `-s`) for the PR report."""

import random
from collections.abc import AsyncIterator
from dataclasses import dataclass

import chess
import pytest
import pytest_asyncio

from chess_tutor.concepts.taxonomy import Concept
from chess_tutor.engines.human_moves import HumanMovePredictor
from chess_tutor.engines.opponent import Maia3Opponent
from chess_tutor.engines.steering import CONCEPT_MOTIFS, SteeringOpponent
from chess_tutor.engines.stockfish import Budget, StockfishPool
from chess_tutor.facts.packet import build_fact_packet

pytestmark = [pytest.mark.maia, pytest.mark.asyncio(loop_scope="module")]

ELO = 1400
GAMES = 4
OPPONENT_MOVES_PER_GAME = 12
MATE = 10_000
REPEATS = 5  # both engines sample, so each position is replayed to cut the noise


@dataclass
class Simulation:
    positions: list[chess.Board]
    maia: Maia3Opponent
    predictor: HumanMovePredictor
    pool: StockfishPool


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def simulation() -> AsyncIterator[Simulation]:
    async with (
        HumanMovePredictor() as predictor,
        StockfishPool("/usr/games/stockfish", 2) as pool,
        Maia3Opponent() as maia,
    ):
        chooser = random.Random(7)
        positions: list[chess.Board] = []
        for _ in range(
            GAMES
        ):  # the player (white) plays Maia-like moves; the opponent is plain Maia
            board = chess.Board()
            while len(board.move_stack) < 2 * OPPONENT_MOVES_PER_GAME and not board.is_game_over():
                if board.turn == chess.WHITE:
                    options = await predictor.predict_human_moves(board, ELO, 5)
                    weights = [option.probability for option in options]
                    board.push(chooser.choices([o.move for o in options], weights)[0])
                else:
                    positions.append(board.copy())
                    board.push(await maia.choose_move(board, ELO))
        yield Simulation(positions, maia, predictor, pool)


def creates_motif(before: chess.Board, move: chess.Move, concept: Concept) -> bool:
    already_present = {fact.id for fact in build_fact_packet(before).facts}
    after = before.copy(stack=False)
    after.push(move)
    return any(
        fact.type in CONCEPT_MOTIFS[concept] and fact.id not in already_present
        for fact in build_fact_packet(after).facts
    )


async def centipawn_loss(pool: StockfishPool, board: chess.Board, move: chess.Move) -> int:
    best = (await pool.analyse(board, Budget.QUICK))[0].side_to_move_score.score(mate_score=MATE)
    after = board.copy(stack=False)
    after.push(move)
    if after.is_game_over():
        return 0
    reached = (await pool.analyse(after, Budget.QUICK))[0].side_to_move_score.score(mate_score=MATE)
    return max(0, (best or 0) + (reached or 0))  # reached is the other side's view


@pytest.mark.parametrize(
    ("concept", "must_beat_baseline"),
    # forks are too rare among the top four human moves to assert on at this sample size
    [(Concept.HANGING_PIECE, True), (Concept.ALLOWED_FORK, False)],
)
async def test_steering_creates_more_focus_motifs_at_a_small_strength_cost(
    simulation: Simulation, concept: Concept, must_beat_baseline: bool
) -> None:
    steering = SteeringOpponent(
        simulation.maia,
        simulation.predictor,
        simulation.pool,
        lambda: frozenset({concept}),
        bias=6.0,
        random_source=random.Random(3),
    )
    counts = {"baseline": 0, "steered": 0}
    losses = {"baseline": 0, "steered": 0}
    for board in simulation.positions * REPEATS:
        for name, engine in (("baseline", simulation.maia), ("steered", steering)):
            move = await engine.choose_move(board, ELO)
            counts[name] += creates_motif(board, move, concept)
            losses[name] += await centipawn_loss(simulation.pool, board, move)
    total = len(simulation.positions) * REPEATS
    print(
        f"\nsteering simulation: {total} opponent moves, focus={concept.value}, "
        f"motif positions baseline={counts['baseline']} steered={counts['steered']}, "
        f"mean centipawn loss baseline={losses['baseline'] / total:.1f} "
        f"steered={losses['steered'] / total:.1f}"
    )

    assert counts["steered"] > counts["baseline"] or not must_beat_baseline
    assert losses["steered"] / total <= losses["baseline"] / total + 30  # the configured cap
