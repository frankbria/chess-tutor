import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal

from pydantic import BaseModel

from chess_tutor.games.clock import ClockState
from chess_tutor.models import GameStatus, PlayerColor


class OpponentMoveEvent(BaseModel):
    type: Literal["opponent_move"] = "opponent_move"
    uci: str
    san: str
    fen: str
    clock: ClockState | None


class SnapshotEvent(BaseModel):
    type: Literal["snapshot"] = "snapshot"
    fen: str
    side_to_move: PlayerColor
    status: GameStatus
    last_move: str | None
    clock: ClockState | None


class GameOverEvent(BaseModel):
    type: Literal["game_over"] = "game_over"
    result: str
    reason: str


class CoachCommentEvent(BaseModel):
    type: Literal["coach_comment"] = "coach_comment"
    ply: int
    text: str
    classification: str
    fact_ids: list[str]
    source: str
    hint_level: int | None = None  # Active mode's ladder rung (1 to 3); null for other comments


GameEvent = OpponentMoveEvent | GameOverEvent | CoachCommentEvent


class GameEvents:
    """In-process pub/sub keyed by game id: one queue per subscriber.

    Call `publish` only from the event loop thread (asyncio queues are not thread-safe).

    shortcut: single process only, so a second API worker would not see these events;
    move to Redis or similar before running more than one worker.
    """

    def __init__(self) -> None:
        self._subscribers: dict[int, set[asyncio.Queue[GameEvent]]] = {}

    @contextmanager
    def subscribe(self, game_id: int) -> Iterator[asyncio.Queue[GameEvent]]:
        queue: asyncio.Queue[GameEvent] = asyncio.Queue()
        self._subscribers.setdefault(game_id, set()).add(queue)
        try:
            yield queue
        finally:
            subscribers = self._subscribers[game_id]
            subscribers.discard(queue)
            if not subscribers:
                del self._subscribers[game_id]

    def publish(self, game_id: int, event: GameEvent) -> None:
        for queue in self._subscribers.get(game_id, ()):
            queue.put_nowait(event)
