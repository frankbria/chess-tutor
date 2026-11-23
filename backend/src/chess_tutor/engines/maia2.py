import asyncio
import json
import logging
from pathlib import Path
from types import TracebackType
from typing import Literal, Self

import chess
from pydantic import BaseModel, ValidationError

from chess_tutor.engines.opponent import OpponentError
from chess_tutor.settings import REPOSITORY_ROOT, Settings

logger = logging.getLogger(__name__)

MOVE_SCRIPT = REPOSITORY_ROOT / "tools" / "maia2_move" / "maia2_move.py"
DEFAULT_PYTHON = Path.home() / ".local" / "share" / "chess-tutor" / "maia2-venv" / "bin" / "python"


class _Ready(BaseModel):
    ready: Literal[True]


class _Response(BaseModel):
    uci: str | None = None
    error: str | None = None


class Maia2Opponent:
    """Maia-2 (MIT) in its own interpreter, so torch stays out of the backend's dependencies."""

    def __init__(
        self,
        python: str = "",
        startup_timeout_seconds: float = 120,
        request_timeout_seconds: float = 10,
    ) -> None:
        self._python = python or str(DEFAULT_PYTHON)
        self._startup_timeout = startup_timeout_seconds
        self._request_timeout = request_timeout_seconds
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        return cls(
            settings.maia2_python,
            settings.maia_policy_startup_timeout_seconds,
            settings.maia_policy_timeout_seconds,
        )

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
        await self.close()
        logger.info("Starting Maia-2 opponent: %s", self._python)
        try:
            self._process = await asyncio.create_subprocess_exec(
                self._python,
                str(MOVE_SCRIPT),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            _Ready.model_validate_json(await self._read_line(self._startup_timeout))
        except (OSError, ValidationError, OpponentError) as error:
            await self.close()
            raise OpponentError(
                f"Maia-2 failed to start ({error}); run scripts/install_maia2.sh"
            ) from error

    async def close(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        if process.returncode is None:
            process.kill()
        await process.wait()

    async def _read_line(self, timeout: float) -> bytes:
        assert self._process is not None and self._process.stdout is not None
        try:
            line = await asyncio.wait_for(self._process.stdout.readline(), timeout)
        except TimeoutError:
            raise OpponentError(f"Maia-2 timed out after {timeout}s") from None
        if not line:
            raise OpponentError("Maia-2 exited unexpectedly")
        return line

    async def choose_move(self, board: chess.Board, elo: int) -> chess.Move:
        if self._process is None or self._process.stdin is None:
            raise OpponentError("Maia-2 opponent is not started")
        if board.is_game_over():
            raise OpponentError("cannot choose a move: the game is over")
        request = json.dumps({"fen": board.fen(), "elo": elo}) + "\n"
        async with self._lock:
            try:
                self._process.stdin.write(request.encode())
                await self._process.stdin.drain()
                line = await self._read_line(self._request_timeout)
            except (OpponentError, ConnectionError):
                await self.close()  # an unresponsive helper cannot be trusted with the next move
                raise
        try:
            response = _Response.model_validate_json(line)
        except ValidationError as error:
            raise OpponentError(f"Maia-2 sent a bad response: {error}") from error
        if response.error is not None or response.uci is None:
            raise OpponentError(f"Maia-2: {response.error}")
        move = chess.Move.from_uci(response.uci)
        if move not in board.legal_moves:
            raise OpponentError(f"Maia-2 returned an illegal move {move} for {board.fen()}")
        return move
