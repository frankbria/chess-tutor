import asyncio
import logging
from types import TracebackType
from typing import Protocol, Self

import chess
import chess.engine

from chess_tutor.settings import Settings

logger = logging.getLogger(__name__)


class OpponentError(RuntimeError):
    pass


class OpponentEngine(Protocol):
    async def start(self) -> None: ...

    async def close(self) -> None: ...

    async def choose_move(self, board: chess.Board, elo: int) -> chess.Move: ...


class Maia3Opponent:
    """Maia-3 as a separate UCI process (AGPL-3 stays outside our dependency tree)."""

    def __init__(self, command: str = "maia3-uci", model: str = "5m", device: str = "cpu") -> None:
        self._command = command
        self._model = model
        self._device = device
        self._engine: chess.engine.UciProtocol | None = None
        self._lock = asyncio.Lock()

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        return cls(settings.maia_command, settings.maia_model, settings.maia_device)

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
        logger.info("Starting Maia-3 opponent: model=%s device=%s", self._model, self._device)
        _, self._engine = await chess.engine.popen_uci(
            [self._command, "--model", self._model, "--device", self._device]
        )

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
            raise OpponentError("Maia-3 opponent is not started")
        if board.is_game_over():
            raise OpponentError("cannot choose a move: the game is over")
        async with self._lock:
            # "Elo" sets both SelfElo and OppoElo: the tutor does not model the human's strength
            await self._engine.configure({"Elo": elo})
            result = await self._engine.play(board, chess.engine.Limit())
        move = result.move
        if move is None or move not in board.legal_moves:
            raise OpponentError(f"Maia-3 returned an illegal move {move} for {board.fen()}")
        return move
