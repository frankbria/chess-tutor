from collections.abc import AsyncIterator

import chess
import pytest
import pytest_asyncio

from chess_tutor.engines.opponent import OpponentError
from chess_tutor.engines.stockfish_limited import StockfishLimitedOpponent
from chess_tutor.settings import Settings

pytestmark = pytest.mark.asyncio(loop_scope="module")

POSITIONS = [
    chess.STARTING_FEN,
    "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 4 4",
    "8/5pk1/6p1/8/8/6P1/5PK1/3R4 w - - 0 1",
]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def opponent() -> AsyncIterator[StockfishLimitedOpponent]:
    async with StockfishLimitedOpponent() as engine:
        yield engine


@pytest.mark.parametrize("fen", POSITIONS)
@pytest.mark.parametrize("elo", [800, 1500, 2400, 3500])
async def test_returns_legal_move(opponent: StockfishLimitedOpponent, fen: str, elo: int) -> None:
    board = chess.Board(fen)
    assert await opponent.choose_move(board, elo) in board.legal_moves


async def test_elo_range_comes_from_the_engine(opponent: StockfishLimitedOpponent) -> None:
    assert (opponent.min_elo, opponent.max_elo) == (1320, 3190)


@pytest.mark.parametrize(("elo", "configured"), [(100, 1320), (9999, 3190), (2000, 2000)])
async def test_elo_is_clamped_to_the_engine_range(
    opponent: StockfishLimitedOpponent, elo: int, configured: int
) -> None:
    await opponent.choose_move(chess.Board(), elo)
    engine = opponent._engine
    assert engine is not None
    assert engine.config["UCI_Elo"] == configured
    assert engine.config["UCI_LimitStrength"] is True


async def test_rejects_finished_game(opponent: StockfishLimitedOpponent) -> None:
    with pytest.raises(OpponentError):
        await opponent.choose_move(chess.Board("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1"), 1500)


async def test_requires_start() -> None:
    with pytest.raises(OpponentError):
        await StockfishLimitedOpponent().choose_move(chess.Board(), 1500)


async def test_close_twice_then_choose_move_raises() -> None:
    engine = StockfishLimitedOpponent()
    await engine.start()
    await engine.close()
    await engine.close()
    with pytest.raises(OpponentError):
        await engine.choose_move(chess.Board(), 1500)


async def test_settings_drive_the_engine() -> None:
    settings = Settings(_env_file=None, llm_provider="none", opponent_stockfish_move_seconds=0.05)
    async with StockfishLimitedOpponent.from_settings(settings) as engine:
        assert await engine.choose_move(chess.Board(), 1500) in chess.Board().legal_moves
