import asyncio
import logging
from types import TracebackType
from typing import Self

import chess
import chess.engine

from chess_tutor.engines.opponent import OpponentError
from chess_tutor.settings import Settings

logger = logging.getLogger(__name__)

STARTUP_TIMEOUT_SECONDS = 10


class StockfishLimitedOpponent:
    """A dedicated Stockfish process playing at a reduced UCI_Elo (the strong end of the range)."""

    def __init__(self, path: str = "/usr/games/stockfish", move_seconds: float = 0.1) -> None:
        self._path = path
        self._move_seconds = move_seconds
        self._engine: chess.engine.UciProtocol | None = None
        self._lock = asyncio.Lock()
        self.min_elo = 0
        self.max_elo = 0

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        return cls(settings.stockfish_path, settings.opponent_stockfish_move_seconds)

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()

    async def start(self) -> None:
        try:
            _, engine = await asyncio.wait_for(
                chess.engine.popen_uci(self._path), timeout=STARTUP_TIMEOUT_SECONDS
            )
        except (OSError, TimeoutError, chess.engine.EngineError) as error:
            raise OpponentError(f"cannot start a UCI engine at {self._path}: {error!r}") from error
        option = engine.options.get("UCI_Elo")
        if option is None or option.min is None or option.max is None:
            await engine.quit()
            raise OpponentError(f"{self._path} does not support UCI_Elo strength limiting")
        self.min_elo, self.max_elo = option.min, option.max
        self._engine = engine
        logger.info("Stockfish-limited opponent: UCI_Elo %s-%s", self.min_elo, self.max_elo)

    async def close(self) -> None:
        engine, self._engine = self._engine, None
        if engine is None:
            return
        try:
            await asyncio.wait_for(engine.quit(), timeout=2)
        except (TimeoutError, chess.engine.EngineError):
            if engine.transport is not None:
                engine.transport.kill()

    async def choose_move(self, board: chess.Board, elo: int) -> chess.Move:
        if self._engine is None:
            raise OpponentError("Stockfish-limited opponent is not started")
        if board.is_game_over():
            raise OpponentError("cannot choose a move: the game is over")
        async with self._lock:
            await self._engine.configure(
                {
                    "UCI_LimitStrength": True,
                    "UCI_Elo": min(max(elo, self.min_elo), self.max_elo),
                }
            )
            result = await self._engine.play(board, chess.engine.Limit(time=self._move_seconds))
        move = result.move
        if move is None or move not in board.legal_moves:
            raise OpponentError(f"Stockfish returned an illegal move {move} for {board.fen()}")
        return move
