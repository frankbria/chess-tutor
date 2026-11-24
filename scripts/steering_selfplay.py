"""Self-play calibration of opponent steering (issue #96): steered Maia-3 against plain Maia-3 at
the same Elo, colours alternating. Games are appended to a JSONL file as they finish, so an
interrupted run resumes where it stopped. Not part of CI; see docs/steering-calibration.md.

    cd backend && uv run python ../scripts/steering_selfplay.py --elo 1200 --games 100 \
        --workers 2 --output /tmp/selfplay-1200.jsonl
"""

import argparse
import asyncio
import json
import random
import time
from pathlib import Path

import chess

from chess_tutor.concepts.taxonomy import Concept
from chess_tutor.engines.calibration import adjudicate, estimate_elo
from chess_tutor.engines.human_moves import HumanMovePredictor
from chess_tutor.engines.opponent import Maia3Opponent, OpponentEngine
from chess_tutor.engines.steering import MATE_SCORE_CENTIPAWNS, SteeringOpponent
from chess_tutor.engines.stockfish import Budget, StockfishPool

# A realistic beginner focus: every steerable concept the tutor most often assigns FOCUS.
FOCUS_CONCEPTS = frozenset({Concept.HANGING_PIECE, Concept.MISSED_FORK})
STOCKFISH_PATH = "/usr/games/stockfish"


async def play_game(
    steered_is_white: bool,
    elo: int,
    rate: float,
    max_plies: int,
    seed: int,
    maia: Maia3Opponent,
    predictor: HumanMovePredictor,
    pool: StockfishPool,
) -> dict[str, object]:
    steered = SteeringOpponent(
        maia,
        predictor,
        pool,
        lambda: FOCUS_CONCEPTS,
        rate=rate,
        random_source=random.Random(seed),
    )
    steered_colour = chess.WHITE if steered_is_white else chess.BLACK
    board = chess.Board()
    while not board.is_game_over(claim_draw=True) and len(board.move_stack) < max_plies:
        engine: OpponentEngine = steered if board.turn == steered_colour else maia
        board.push(await engine.choose_move(board, elo))
    outcome = board.outcome(claim_draw=True)
    adjudicated = outcome is None
    if outcome is not None:
        white_result = 0.5 if outcome.winner is None else float(outcome.winner == chess.WHITE)
    else:
        (line,) = await pool.analyse(board, Budget.STANDARD)
        white_result = adjudicate(line.white_score.score(mate_score=MATE_SCORE_CENTIPAWNS) or 0)
    return {
        "steered_is_white": steered_is_white,
        "steered_result": white_result if steered_is_white else 1 - white_result,
        "plies": len(board.move_stack),
        "adjudicated": adjudicated,
    }


async def worker(
    queue: asyncio.Queue[int], elo: int, rate: float, max_plies: int, output: Path
) -> None:
    async with (
        HumanMovePredictor() as predictor,
        Maia3Opponent() as maia,
        StockfishPool(STOCKFISH_PATH, 1) as pool,
    ):
        while True:
            try:
                index = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            started = time.monotonic()
            game = await play_game(
                index % 2 == 0, elo, rate, max_plies, index, maia, predictor, pool
            )
            game |= {
                "elo": elo,
                "rate": rate,
                "max_plies": max_plies,
                "index": index,
                "seconds": round(time.monotonic() - started, 1),
            }
            with output.open("a") as file:
                file.write(json.dumps(game) + "\n")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--elo", type=int, required=True)
    parser.add_argument("--games", type=int, required=True)
    parser.add_argument("--workers", type=int, default=2, help="3 engine processes per worker")
    parser.add_argument("--rate", type=float, default=0.25)
    parser.add_argument("--max-plies", type=int, default=150)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()

    settings = {"elo": arguments.elo, "rate": arguments.rate, "max_plies": arguments.max_plies}
    previous = (
        [json.loads(line) for line in arguments.output.read_text().splitlines()]
        if arguments.output.exists()
        else []
    )
    for game in previous:
        recorded = {name: game.get(name) for name in settings}
        if recorded != settings:
            raise SystemExit(
                f"{arguments.output} holds games played with {recorded}, not {settings}; "
                "use a new output file"
            )
    finished = {game["index"] for game in previous}
    queue: asyncio.Queue[int] = asyncio.Queue()
    for index in range(arguments.games):
        if index not in finished:
            queue.put_nowait(index)
    await asyncio.gather(
        *(
            worker(queue, arguments.elo, arguments.rate, arguments.max_plies, arguments.output)
            for _ in range(arguments.workers)
        )
    )

    games = (
        [json.loads(line) for line in arguments.output.read_text().splitlines()]
        if arguments.output.exists()
        else []
    )
    if len(games) < 2:
        raise SystemExit(f"{len(games)} game(s) finished; at least 2 are needed for an interval")
    estimate = estimate_elo([float(game["steered_result"]) for game in games])
    seconds = sum(float(game["seconds"]) for game in games)
    print(
        f"elo={arguments.elo} rate={arguments.rate} games={len(games)} "
        f"adjudicated={sum(bool(game['adjudicated']) for game in games)} "
        f"score={estimate.score:.3f} elo_difference={estimate.elo_difference:+.0f} "
        f"ci95=[{estimate.lower:+.0f}, {estimate.upper:+.0f}] "
        f"mean_game_seconds={seconds / len(games):.0f}"
    )


if __name__ == "__main__":
    asyncio.run(main())
