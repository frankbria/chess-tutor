import asyncio
import json
import logging
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Self

import chess
from pydantic import BaseModel

from chess_tutor.settings import REPOSITORY_ROOT, Settings

logger = logging.getLogger(__name__)

POLICY_SCRIPT = REPOSITORY_ROOT / "tools" / "maia3_policy" / "maia3_policy.py"


class HumanMovePredictionError(RuntimeError):
    pass


class _HelperMove(BaseModel):
    uci: str
    probability: float


class _HelperResponse(BaseModel):
    moves: list[_HelperMove] = []
    error: str | None = None


@dataclass(frozen=True)
class HumanMovePrediction:
    move: chess.Move
    probability: float


def default_policy_command() -> list[str]:
    """The maia3 uv tool's own interpreter running the AGPL policy helper."""
    tool_directory = subprocess.run(
        ["uv", "tool", "dir"], capture_output=True, text=True, check=True
    ).stdout.strip()
    return [str(Path(tool_directory) / "maia3" / "bin" / "python"), str(POLICY_SCRIPT)]


class HumanMovePredictor:
    """Long-lived out-of-process Maia-3 policy helper (JSON lines over stdin/stdout)."""

    def __init__(
        self, command: list[str] | None = None, model: str = "5m", device: str = "cpu"
    ) -> None:
        self._command = command
        self._model = model
        self._device = device
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        command = shlex.split(settings.maia_policy_command) or None
        return cls(command, settings.maia_model, settings.maia_device)

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
        command = self._command or default_policy_command()
        logger.info("Starting Maia-3 policy helper: model=%s device=%s", self._model, self._device)
        self._process = await asyncio.create_subprocess_exec(
            *command,
            "--model",
            self._model,
            "--device",
            self._device,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await self._read_response()
        except HumanMovePredictionError:
            await self.close()
            raise

    async def close(self) -> None:
        process, self._process = self._process, None
        if process is None or process.returncode is not None:
            return
        process.kill()
        await process.wait()

    async def _read_response(self) -> _HelperResponse:
        if self._process is None or self._process.stdout is None:
            raise HumanMovePredictionError("Maia-3 policy helper is not started")
        line = await self._process.stdout.readline()
        if not line:
            raise HumanMovePredictionError("Maia-3 policy helper exited unexpectedly")
        response = _HelperResponse.model_validate_json(line)
        if response.error is not None:
            raise HumanMovePredictionError(f"Maia-3 policy helper: {response.error}")
        return response

    async def predict_human_moves(
        self, board: chess.Board, elo: int, k: int
    ) -> list[HumanMovePrediction]:
        if self._process is None or self._process.stdin is None:
            raise HumanMovePredictionError("Maia-3 policy helper is not started")
        if k < 1:
            raise HumanMovePredictionError("k must be at least 1")
        request = json.dumps({"fen": board.fen(), "elo": elo, "k": k}) + "\n"
        async with self._lock:
            self._process.stdin.write(request.encode())
            await self._process.stdin.drain()
            response = await self._read_response()
        predictions = []
        for item in response.moves:
            move = chess.Move.from_uci(item.uci)
            if move not in board.legal_moves:
                raise HumanMovePredictionError(
                    f"Maia-3 policy helper returned an illegal move {move} for {board.fen()}"
                )
            predictions.append(HumanMovePrediction(move, item.probability))
        return predictions
