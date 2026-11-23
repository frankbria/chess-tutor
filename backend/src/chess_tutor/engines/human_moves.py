import asyncio
import json
import logging
import shlex
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Literal, Self

import chess
from pydantic import BaseModel, ValidationError

from chess_tutor.settings import REPOSITORY_ROOT, Settings

logger = logging.getLogger(__name__)

POLICY_SCRIPT = REPOSITORY_ROOT / "tools" / "maia3_policy" / "maia3_policy.py"


class HumanMovePredictionError(RuntimeError):
    pass


class _HelperMove(BaseModel):
    uci: str
    probability: float


class _HelperReady(BaseModel):
    ready: Literal[True]


class _HelperResponse(BaseModel):
    moves: list[_HelperMove] = []
    error: str | None = None


@dataclass(frozen=True)
class HumanMovePrediction:
    move: chess.Move
    probability: float


_default_command: list[str] | None = None


async def default_policy_command() -> list[str]:
    """The maia3 uv tool's own interpreter running the AGPL policy helper (resolved once)."""
    global _default_command
    if _default_command is None:
        lookup = await asyncio.create_subprocess_exec(
            "uv", "tool", "dir", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        output, _ = await lookup.communicate()
        if lookup.returncode != 0:
            raise HumanMovePredictionError("cannot locate the maia3 uv tool (`uv tool dir` failed)")
        tool_directory = Path(output.decode().strip())
        _default_command = [str(tool_directory / "maia3" / "bin" / "python"), str(POLICY_SCRIPT)]
    return _default_command


STDERR_TAIL_LINES = 20
PROBABILITY_SUM_TOLERANCE = 1e-6


class HumanMovePredictor:
    """Long-lived out-of-process Maia-3 policy helper (JSON lines over stdin/stdout)."""

    def __init__(
        self,
        command: list[str] | None = None,
        model: str = "5m",
        device: str = "cpu",
        startup_timeout_seconds: float = 60,
        request_timeout_seconds: float = 10,
    ) -> None:
        self._command = command
        self._model = model
        self._device = device
        self._startup_timeout = startup_timeout_seconds
        self._request_timeout = request_timeout_seconds
        self._process: asyncio.subprocess.Process | None = None
        self._stderr_tail: deque[str] = deque(maxlen=STDERR_TAIL_LINES)
        self._stderr_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        command = shlex.split(settings.maia_policy_command) or None
        return cls(
            command,
            settings.maia_model,
            settings.maia_device,
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
        try:
            command = self._command or await default_policy_command()
            logger.info(
                "Starting Maia-3 policy helper: model=%s device=%s", self._model, self._device
            )
            self._process = await asyncio.create_subprocess_exec(
                *command,
                "--model",
                self._model,
                "--device",
                self._device,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._stderr_task = asyncio.create_task(self._collect_stderr(self._process))
            line = await self._read_line(self._startup_timeout)
            _HelperReady.model_validate_json(line)
        except BaseException as error:
            await self.close()
            if isinstance(error, HumanMovePredictionError):
                raise
            if isinstance(error, Exception):
                raise HumanMovePredictionError(
                    f"Maia-3 policy helper failed to start: {error!r}{self._stderr_report()}"
                ) from error
            raise

    async def close(self) -> None:
        process, self._process = self._process, None
        stderr_task, self._stderr_task = self._stderr_task, None
        if process is not None and process.returncode is None:
            process.kill()
        if process is not None:
            await process.wait()
        if stderr_task is not None:
            await asyncio.gather(stderr_task, return_exceptions=True)

    async def _collect_stderr(self, process: asyncio.subprocess.Process) -> None:
        assert process.stderr is not None
        async for line in process.stderr:
            self._stderr_tail.append(line.decode(errors="replace").rstrip())

    def _stderr_report(self) -> str:
        return "\nhelper stderr:\n" + "\n".join(self._stderr_tail) if self._stderr_tail else ""

    async def _fail(self, message: str, helper_exited: bool = False) -> HumanMovePredictionError:
        # the helper is unusable after any failure: stop it so a later start() can relaunch
        if helper_exited and self._stderr_task is not None:
            await asyncio.wait({self._stderr_task}, timeout=1)  # let it read the last lines
        report = self._stderr_report()
        await self.close()
        return HumanMovePredictionError(message + report)

    async def _read_line(self, timeout: float) -> bytes:
        if self._process is None or self._process.stdout is None:
            raise HumanMovePredictionError("Maia-3 policy helper is not started")
        try:
            line = await asyncio.wait_for(self._process.stdout.readline(), timeout)
        except TimeoutError:
            raise await self._fail(f"Maia-3 policy helper timed out after {timeout}s") from None
        except (ConnectionResetError, ValueError) as error:
            raise await self._fail(f"Maia-3 policy helper read failed: {error!r}") from error
        if not line:
            raise await self._fail("Maia-3 policy helper exited unexpectedly", helper_exited=True)
        return line

    async def predict_human_moves(
        self, board: chess.Board, elo: int, k: int
    ) -> list[HumanMovePrediction]:
        if k < 1:
            raise HumanMovePredictionError("k must be at least 1")
        request = json.dumps({"fen": board.fen(), "elo": elo, "k": k}) + "\n"
        async with self._lock:
            process = self._process
            if process is None or process.stdin is None:
                raise HumanMovePredictionError("Maia-3 policy helper is not started")
            try:
                process.stdin.write(request.encode())
                await asyncio.wait_for(process.stdin.drain(), self._request_timeout)
            except TimeoutError:
                raise await self._fail("Maia-3 policy helper stopped reading requests") from None
            except (BrokenPipeError, ConnectionResetError) as error:
                raise await self._fail(f"Maia-3 policy helper died: {error!r}") from error
            line = await self._read_line(self._request_timeout)
            try:
                response = _HelperResponse.model_validate_json(line)
            except ValidationError as error:
                raise await self._fail(
                    f"Maia-3 policy helper sent a bad response: {error}"
                ) from error
        if response.error is not None:
            raise HumanMovePredictionError(f"Maia-3 policy helper: {response.error}")
        return self._validated(board, response.moves)

    @staticmethod
    def _validated(board: chess.Board, moves: list[_HelperMove]) -> list[HumanMovePrediction]:
        probabilities = [item.probability for item in moves]
        if any(not 0 <= probability <= 1 for probability in probabilities):
            raise HumanMovePredictionError(f"probabilities outside [0, 1]: {probabilities}")
        if probabilities != sorted(probabilities, reverse=True):
            raise HumanMovePredictionError(
                f"probabilities are not sorted descending: {probabilities}"
            )
        if sum(probabilities) > 1 + PROBABILITY_SUM_TOLERANCE:
            raise HumanMovePredictionError(f"probabilities sum above 1: {sum(probabilities)}")
        predictions = []
        for item in moves:
            try:
                move = chess.Move.from_uci(item.uci)
            except ValueError as error:
                raise HumanMovePredictionError(f"unparseable move {item.uci!r}") from error
            if move not in board.legal_moves:
                raise HumanMovePredictionError(
                    f"Maia-3 policy helper returned an illegal move {move} for {board.fen()}"
                )
            predictions.append(HumanMovePrediction(move, item.probability))
        return predictions
