import asyncio
from dataclasses import dataclass
from enum import Enum
from types import TracebackType
from typing import Self

import chess
import chess.engine


class Budget(Enum):
    """How hard Stockfish thinks. Fixed depth keeps results reproducible across machines
    (time limits would vary with load): quick for per-move triage, standard for normal
    classification, deep for critical positions and post-game review."""

    QUICK = chess.engine.Limit(depth=10)
    STANDARD = chess.engine.Limit(depth=16)
    DEEP = chess.engine.Limit(depth=22)


@dataclass(frozen=True)
class AnalysedLine:
    white_score: chess.engine.Score
    side_to_move_score: chess.engine.Score
    pv: list[chess.Move]


class StockfishPool:
    """A fixed set of long-lived Stockfish processes; extra requests queue for a free one."""

    def __init__(self, path: str, size: int) -> None:
        self._path = path
        self._size = size
        self._engines: list[chess.engine.UciProtocol] = []
        self._idle: asyncio.Queue[chess.engine.UciProtocol] = asyncio.Queue()

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
        for _ in range(self._size):
            self._add(await self._spawn())

    async def close(self) -> None:
        engines, self._engines = self._engines, []
        self._idle = asyncio.Queue()
        await asyncio.gather(*(self._quit(engine) for engine in engines))

    @property
    def in_use(self) -> int:
        return len(self._engines) - self._idle.qsize()

    def engine_pids(self) -> list[int]:
        return [self._pid(engine) for engine in self._engines]

    async def analyse(
        self, board: chess.Board, budget: Budget, multipv: int = 1
    ) -> list[AnalysedLine]:
        engine = await self._idle.get()
        try:
            try:
                infos = await engine.analyse(board, budget.value, multipv=multipv)
            except chess.engine.EngineTerminatedError:
                engine = await self._replace(engine)
                infos = await engine.analyse(board, budget.value, multipv=multipv)
        finally:
            self._idle.put_nowait(engine)
        return [
            AnalysedLine(
                white_score=info["score"].white(),
                side_to_move_score=info["score"].pov(board.turn),
                pv=info.get("pv", []),
            )
            for info in infos
        ]

    async def _spawn(self) -> chess.engine.UciProtocol:
        _, engine = await chess.engine.popen_uci(self._path)
        return engine

    def _add(self, engine: chess.engine.UciProtocol) -> None:
        self._engines.append(engine)
        self._idle.put_nowait(engine)

    async def _replace(self, dead: chess.engine.UciProtocol) -> chess.engine.UciProtocol:
        # shortcut: a failed respawn propagates and leaves the pool one engine short, add retry
        # with backoff if spawn failures ever become routine
        self._engines.remove(dead)
        await self._quit(dead)
        fresh = await self._spawn()
        self._engines.append(fresh)
        return fresh

    @staticmethod
    def _pid(engine: chess.engine.UciProtocol) -> int:
        transport = engine.transport
        assert isinstance(transport, asyncio.SubprocessTransport)
        return transport.get_pid()

    @staticmethod
    async def _quit(engine: chess.engine.UciProtocol) -> None:
        try:
            await asyncio.wait_for(engine.quit(), timeout=2)
        except (chess.engine.EngineError, TimeoutError, asyncio.CancelledError):
            if engine.transport is not None:
                engine.transport.kill()
