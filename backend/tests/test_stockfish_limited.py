from collections.abc import AsyncIterator

import chess
import chess.engine
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


async def _reported_elo_range() -> tuple[int, int]:
    _, probe = await chess.engine.popen_uci(
        Settings(_env_file=None, llm_provider="none").stockfish_path
    )
    try:
        option = probe.options["UCI_Elo"]
        assert option.min is not None and option.max is not None
        return option.min, option.max
    finally:
        await probe.quit()


async def test_elo_range_comes_from_the_engine(opponent: StockfishLimitedOpponent) -> None:
    assert (opponent.min_elo, opponent.max_elo) == await _reported_elo_range()


async def test_elo_is_clamped_to_the_engine_range(opponent: StockfishLimitedOpponent) -> None:
    low, high = await _reported_elo_range()
    engine = opponent._engine
    assert engine is not None
    for requested, expected in [(low - 500, low), (high + 500, high), (low + 100, low + 100)]:
        await opponent.choose_move(chess.Board(), requested)
        assert engine.config["UCI_Elo"] == expected
    assert engine.config["UCI_LimitStrength"] is True


async def test_missing_binary_raises_opponent_error_naming_the_path() -> None:
    with pytest.raises(OpponentError, match="/nonexistent/stockfish"):
        await StockfishLimitedOpponent(path="/nonexistent/stockfish").start()


async def test_non_uci_program_raises_opponent_error_and_leaves_no_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("chess_tutor.engines.stockfish_limited.STARTUP_TIMEOUT_SECONDS", 0.5)
    engine = StockfishLimitedOpponent(path="/bin/cat")
    with pytest.raises(OpponentError, match="/bin/cat"):
        await engine.start()
    assert engine._engine is None


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
