import logging
from collections.abc import AsyncIterator

import chess
import chess.engine
import pytest
import pytest_asyncio

from chess_tutor.engines.opponent import Maia3Opponent, OpponentError

pytestmark = [pytest.mark.maia, pytest.mark.asyncio(loop_scope="module")]

POSITIONS = [
    chess.STARTING_FEN,
    "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3",
    "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 4 4",
    "8/5pk1/6p1/8/8/6P1/5PK1/3R4 w - - 0 1",
]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def opponent() -> AsyncIterator[Maia3Opponent]:
    async with Maia3Opponent() as engine:
        yield engine


@pytest.mark.parametrize("elo", [600, 800, 1500])
@pytest.mark.parametrize("fen", POSITIONS)
async def test_returns_legal_move(opponent: Maia3Opponent, fen: str, elo: int) -> None:
    board = chess.Board(fen)
    assert await opponent.choose_move(board, elo) in board.legal_moves


async def test_rejects_finished_game(opponent: Maia3Opponent) -> None:
    board = chess.Board("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1")
    with pytest.raises(OpponentError):
        await opponent.choose_move(board, 1500)


async def test_requires_start() -> None:
    with pytest.raises(OpponentError):
        await Maia3Opponent().choose_move(chess.Board(), 1500)


async def test_model_setting_is_passed_through(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="chess_tutor.engines.opponent")
    async with Maia3Opponent(model="3m", device="cpu") as engine:
        move = await engine.choose_move(chess.Board(), 800)
    assert move in chess.Board().legal_moves
    assert "model=3m device=cpu" in caplog.text


async def test_unknown_model_stops_the_engine() -> None:
    with pytest.raises(chess.engine.EngineError):
        await Maia3Opponent(model="no-such-model").start()
