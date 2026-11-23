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
from chess_tutor.facts.models import Side
from chess_tutor.facts.packet import build_fact_packet

pytestmark = [pytest.mark.maia, pytest.mark.asyncio(loop_scope="module")]

ELO = 1400
GAMES = 4
OPPONENT_MOVES_PER_GAME = 12
MATE = 10_000
DEFAULT_RATE = 0.25
LIFT_RATE = 0.5  # above the default so the motif lift clears the sampling noise
LOSS_CAP = 300
REPEATS = 10  # both engines sample, so each position is replayed to cut the noise


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
    """Side-aware: the player is white, the opponent plays black."""
    already_present = {fact.id for fact in build_fact_packet(before).facts}
    return SteeringOpponent._creates_motif(
        before, move, [CONCEPT_MOTIFS[concept]], already_present, Side.WHITE
    )


async def centipawn_loss(pool: StockfishPool, board: chess.Board, move: chess.Move) -> int:
    best = (await pool.analyse(board, Budget.QUICK))[0].side_to_move_score.score(mate_score=MATE)
    after = board.copy(stack=False)
    after.push(move)
    if after.is_game_over():
        return 0
    reached = (await pool.analyse(after, Budget.QUICK))[0].side_to_move_score.score(mate_score=MATE)
    # reached is the other side's view; capped so a rare Maia blunder cannot dominate the mean
    return min(LOSS_CAP, max(0, (best or 0) + (reached or 0)))


async def measure(
    simulation: Simulation, concept: Concept, rate: float
) -> tuple[dict[str, int], dict[str, float]]:
    steering = SteeringOpponent(
        simulation.maia,
        simulation.predictor,
        simulation.pool,
        lambda: frozenset({concept}),
        rate=rate,
        random_source=random.Random(3),
    )
    counts = {"baseline": 0, "steered": 0}
    losses = {"baseline": 0.0, "steered": 0.0}
    for board in simulation.positions * REPEATS:
        for name, engine in (("baseline", simulation.maia), ("steered", steering)):
            move = await engine.choose_move(board, ELO)
            counts[name] += creates_motif(board, move, concept)
            losses[name] += await centipawn_loss(simulation.pool, board, move)
    total = len(simulation.positions) * REPEATS
    mean_losses = {name: loss / total for name, loss in losses.items()}
    print(
        f"\nsteering simulation: {total} opponent moves, focus={concept.value}, rate={rate}, "
        f"motif positions baseline={counts['baseline']} steered={counts['steered']}, "
        f"mean centipawn loss baseline={mean_losses['baseline']:.1f} "
        f"steered={mean_losses['steered']:.1f}"
    )
    return counts, mean_losses


@pytest.mark.parametrize("concept", [Concept.HANGING_PIECE, Concept.MISSED_FORK])
async def test_default_rate_steering_leaves_the_opponents_strength_unchanged(
    simulation: Simulation, concept: Concept
) -> None:
    _, mean_losses = await measure(simulation, concept, DEFAULT_RATE)

    # Two-sided: steered moves cost at most 30 cp over Maia's first choice, yet the opponent must
    # not get notably weaker or stronger; allow 15% plus 10 cp of sampling noise.
    baseline = mean_losses["baseline"]
    assert abs(mean_losses["steered"] - baseline) <= baseline * 0.15 + 10


async def test_steering_creates_more_player_hanging_pieces_at_a_small_strength_cost(
    simulation: Simulation,
) -> None:
    counts, mean_losses = await measure(simulation, Concept.HANGING_PIECE, LIFT_RATE)

    assert counts["steered"] > counts["baseline"]
    assert mean_losses["steered"] <= mean_losses["baseline"] * 1.15 + 10
