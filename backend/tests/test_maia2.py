from collections.abc import AsyncIterator

import chess
import pytest
import pytest_asyncio

from chess_tutor.engines.maia2 import Maia2Opponent
from chess_tutor.engines.opponent import OpponentError

pytestmark = [pytest.mark.maia2, pytest.mark.asyncio(loop_scope="module")]

POSITIONS = [
    chess.STARTING_FEN,
    "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 4 4",
    "8/5pk1/6p1/8/8/6P1/5PK1/3R4 w - - 0 1",
]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def opponent() -> AsyncIterator[Maia2Opponent]:
    async with Maia2Opponent() as engine:
        yield engine


@pytest.mark.parametrize("elo", [600, 1500, 2500])
@pytest.mark.parametrize("fen", POSITIONS)
async def test_returns_legal_move(opponent: Maia2Opponent, fen: str, elo: int) -> None:
    board = chess.Board(fen)
    assert await opponent.choose_move(board, elo) in board.legal_moves


async def test_rejects_finished_game(opponent: Maia2Opponent) -> None:
    with pytest.raises(OpponentError):
        await opponent.choose_move(chess.Board("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1"), 1500)


async def test_requires_start() -> None:
    with pytest.raises(OpponentError):
        await Maia2Opponent().choose_move(chess.Board(), 1500)


async def test_missing_interpreter_says_how_to_install() -> None:
    with pytest.raises(OpponentError, match="install_maia2"):
        await Maia2Opponent(python="/nonexistent/python").start()


async def test_close_twice_then_choose_move_raises() -> None:
    engine = Maia2Opponent()
    await engine.start()
    await engine.close()
    await engine.close()
    with pytest.raises(OpponentError):
        await engine.choose_move(chess.Board(), 1500)


async def test_silent_helper_times_out_and_stops() -> None:
    engine = Maia2Opponent(request_timeout_seconds=0.001)
    await engine.start()
    with pytest.raises(OpponentError, match="timed out"):
        await engine.choose_move(chess.Board(), 1500)
    with pytest.raises(OpponentError, match="not started"):
        await engine.choose_move(chess.Board(), 1500)
