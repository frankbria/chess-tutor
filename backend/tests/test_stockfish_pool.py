import asyncio
import os
import signal
from collections.abc import AsyncIterator
from pathlib import Path

import chess
import chess.engine
import pytest

from chess_tutor.engines.stockfish import AnalysedLine, Budget, StockfishPool
from chess_tutor.settings import Settings

POOL_SIZE = 2
MATE_IN_ONE = "6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1"


@pytest.fixture
async def pool() -> AsyncIterator[StockfishPool]:
    settings = Settings(_env_file=None, llm_provider="none", stockfish_pool_size=POOL_SIZE)
    async with StockfishPool(settings.stockfish_path, settings.stockfish_pool_size) as started:
        yield started


async def test_finds_mate_in_one(pool: StockfishPool) -> None:
    lines = await pool.analyse(chess.Board(MATE_IN_ONE), Budget.QUICK)

    assert lines[0].pv[0] == chess.Move.from_uci("a1a8")
    assert lines[0].white_score == chess.engine.Mate(1)
    assert lines[0].side_to_move_score == chess.engine.Mate(1)


async def test_scores_are_reported_from_white_and_from_side_to_move(pool: StockfishPool) -> None:
    black_is_mated = chess.Board("7k/8/5QK1/8/8/8/8/8 b - - 0 1")

    lines = await pool.analyse(black_is_mated, Budget.QUICK)

    white_mate = lines[0].white_score.mate()
    side_to_move_mate = lines[0].side_to_move_score.mate()
    assert white_mate is not None and white_mate > 0
    assert side_to_move_mate == -white_mate


async def test_multipv_returns_distinct_lines_best_first(pool: StockfishPool) -> None:
    lines = await pool.analyse(chess.Board(), Budget.QUICK, multipv=3)

    assert len(lines) == 3
    assert len({line.pv[0] for line in lines}) == 3
    scores = [line.side_to_move_score.score(mate_score=100_000) for line in lines]
    assert scores == sorted(scores, reverse=True)


async def test_more_requests_than_engines_all_complete_within_pool_size(
    pool: StockfishPool,
) -> None:
    peak = 0

    async def watch() -> None:
        nonlocal peak
        while True:
            peak = max(peak, pool.in_use)
            await asyncio.sleep(0.001)

    watcher = asyncio.create_task(watch())
    results: list[list[AnalysedLine]] = await asyncio.gather(
        *(pool.analyse(chess.Board(), Budget.QUICK) for _ in range(POOL_SIZE * 3))
    )
    watcher.cancel()

    assert len(results) == POOL_SIZE * 3
    assert all(result for result in results)
    assert peak == POOL_SIZE
    assert pool.in_use == 0


async def test_killed_engine_is_replaced_and_request_still_succeeds(pool: StockfishPool) -> None:
    original_pids = pool.engine_pids()
    assert len(original_pids) == POOL_SIZE
    os.kill(original_pids[0], signal.SIGKILL)
    await asyncio.sleep(0.2)

    results = await asyncio.gather(
        *(pool.analyse(chess.Board(MATE_IN_ONE), Budget.QUICK) for _ in range(POOL_SIZE))
    )

    assert all(result[0].white_score == chess.engine.Mate(1) for result in results)
    pids = pool.engine_pids()
    assert len(pids) == POOL_SIZE
    assert original_pids[0] not in pids
    for pid in pids:
        os.kill(pid, 0)  # raises if the process is gone


async def test_close_terminates_every_engine() -> None:
    started = StockfishPool(Settings(_env_file=None, llm_provider="none").stockfish_path, 2)
    await started.start()
    pids = started.engine_pids()

    await started.close()

    assert started.engine_pids() == []
    for pid in pids:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


async def test_time_limit_is_accepted_instead_of_a_budget(pool: StockfishPool) -> None:
    lines = await pool.analyse(chess.Board(MATE_IN_ONE), chess.engine.Limit(time=0.05))

    assert lines[0].white_score == chess.engine.Mate(1)


async def test_failed_replacement_leaves_pool_one_short_without_the_dead_engine(
    pool: StockfishPool,
) -> None:
    os.kill(pool.engine_pids()[0], signal.SIGKILL)
    await asyncio.sleep(0.2)
    pool._path = "/nonexistent/stockfish"

    with pytest.raises(FileNotFoundError):
        await pool.analyse(chess.Board(), Budget.QUICK)

    assert len(pool.engine_pids()) == POOL_SIZE - 1
    assert pool.in_use == 0
    assert (await pool.analyse(chess.Board(), Budget.QUICK))[0].pv


async def test_failed_start_quits_engines_that_were_already_spawned(tmp_path: Path) -> None:
    marker = tmp_path / "spawned-once"
    script = tmp_path / "flaky-stockfish"
    script.write_text(
        f"#!/bin/sh\nif [ -e {marker} ]; then exit 1; fi\ntouch {marker}\n"
        "exec /usr/games/stockfish\n"
    )
    script.chmod(0o755)
    failing_pool = StockfishPool(str(script), POOL_SIZE)
    before = _stockfish_process_count()

    with pytest.raises(chess.engine.EngineTerminatedError):
        await failing_pool.start()

    assert failing_pool.engine_pids() == []
    assert _stockfish_process_count() == before


def _stockfish_process_count() -> int:
    """Live stockfish processes spawned by this test process (other test runs don't count)."""
    count = 0
    for entry in Path("/proc").iterdir():
        try:
            if not entry.name.isdigit() or (entry / "comm").read_text().strip() != "stockfish":
                continue
            # /proc/<pid>/stat: "pid (comm) state ppid ..."; comm has no spaces for stockfish.
            parent_pid = int((entry / "stat").read_text().split()[3])
        except (FileNotFoundError, ProcessLookupError):
            continue  # the process exited while we were reading it
        count += parent_pid == os.getpid()
    return count
