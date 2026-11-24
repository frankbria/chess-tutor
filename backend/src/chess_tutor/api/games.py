"""Game REST API and the per-game SSE stream.

SSE `GET /games/{id}/coach/stream` emits one JSON object per event, with the SSE `event:` name
equal to the object's `type`:

- `opponent_move`: `{"type", "uci", "san", "fen", "clock"}`: the opponent's move, the resulting FEN
  and the clock after it (null if untimed).
- `snapshot`: `{"type", "fen", "side_to_move", "status", "last_move", "clock"}`, always the first
  event on a new subscription; `last_move` is the SAN of the latest move or null; `clock` is
  `{white_ms, black_ms, running}` for a timed game, else null. A stream for a game that
  is already finished ends right after it.
- `game_over`: `{"type", "result", "reason"}`; `result` is `1-0`, `0-1` or `1/2-1/2`, `reason` is
  `checkmate`, `stalemate`, `insufficient_material`, `fifty_moves`, `threefold_repetition`, etc.,
  `resignation` or `timeout`. The stream closes after it.
  A timed game's stream checks the clock once a second and emits `game_over` on a flag.

- `coach_comment` (Passive mode, when the coach is on): `{"type", "ply", "text", "classification",
  "phase", "fact_ids", "source"}`: the coach's comment on the player's move at `ply` (1-based,
  whole game; `phase` is that position's opening/middlegame/endgame), only for an Inaccuracy
  or worse and for Great/Brilliant moves. It is produced in a background task so the opponent's
  reply is never delayed: expect it after `opponent_move`. When the game ends on the player's
  move or by resignation, `game_over` waits for the final comment (at most 30
  seconds), so the comment comes first; past that cap it is only stored. `source` is `llm`,
  `llm_regenerated` or `template`.

Events are published after the service call that caused them, so subscribe before moving.
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated

import chess
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, Field
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from chess_tutor.api.events import GameEvents, GameOverEvent, OpponentMoveEvent, SnapshotEvent
from chess_tutor.coach.passive import PassiveCoach
from chess_tutor.games.clock import ClockState
from chess_tutor.games.service import (
    ColorChoice,
    GameError,
    GameNotActiveError,
    GameNotFoundError,
    GameService,
    IllegalMoveError,
    InvalidGameSettingsError,
    NotPlayersTurnError,
    OpponentUnavailableError,
)
from chess_tutor.models import (
    CoachMessage,
    CoachMessageKind,
    CoachRole,
    Game,
    GameMode,
    GameResult,
    GameStatus,
    Move,
    PlayerColor,
    TimeControl,
)
from chess_tutor.openings.phase import GamePhase, detect_phase

CLOCK_POLL_SECONDS = 1.0
DEFAULT_PLAYER_ID = 1  # shortcut: single-user app, no auth yet; replace with the signed-in player.

# Ordered: the first isinstance match wins, so list subclasses before their bases.
ERROR_STATUS: tuple[tuple[type[GameError], int], ...] = (
    (GameNotFoundError, 404),
    (IllegalMoveError, 422),
    (InvalidGameSettingsError, 422),
    (NotPlayersTurnError, 409),
    (GameNotActiveError, 409),
    (OpponentUnavailableError, 503),
)


def status_for(error: Exception) -> int:
    return next((status for kind, status in ERROR_STATUS if isinstance(error, kind)), 500)


async def game_error_handler(_request: Request, error: Exception) -> JSONResponse:
    return JSONResponse({"detail": str(error)}, status_code=status_for(error))


def register_game_errors(app: FastAPI) -> None:
    app.add_exception_handler(GameError, game_error_handler)


def get_game_service(request: Request) -> GameService:
    service: GameService | None = getattr(request.app.state, "game_service", None)
    if service is None:
        raise HTTPException(503, "the opponent engine is not running")
    return service


def get_game_events(request: Request) -> GameEvents:
    events: GameEvents = request.app.state.game_events
    return events


def get_passive_coach(request: Request) -> PassiveCoach:
    coach: PassiveCoach = request.app.state.passive_coach
    return coach


Service = Annotated[GameService, Depends(get_game_service)]
Events = Annotated[GameEvents, Depends(get_game_events)]
Coach = Annotated[PassiveCoach, Depends(get_passive_coach)]


def existing_game_id(game_id: int, service: Service) -> int:
    """Checked before the response starts, because errors raised mid-stream can't become a 404."""
    service.get_game(game_id)
    return game_id


class CreateGameRequest(BaseModel):
    mode: GameMode = GameMode.REGULAR
    color: ColorChoice = ColorChoice.RANDOM
    opponent_elo: int | None = Field(
        default=None,
        description="Defaults to the player rating, plus the stretch in tutor modes",
    )
    time_control: TimeControl | None = Field(
        default=None, description="Regular mode only; omit for an untimed game"
    )


class MoveRequest(BaseModel):
    uci: str = Field(examples=["e2e4"])


class GameResponse(BaseModel):
    id: int
    mode: GameMode
    player_color: PlayerColor
    opponent_elo: int
    status: GameStatus
    result: GameResult | None
    accuracy: float | None = Field(description="Player accuracy, null until moves are classified")
    performance_rating: float | None = Field(
        description="Accuracy-based performance estimate, shown alongside (never mixed into) Glicko"
    )
    fen: str
    moves: list[str] = Field(description="Moves in SAN, in play order")
    side_to_move: PlayerColor
    legal_moves: list[str] = Field(description="Legal moves in UCI, empty once the game is over")
    time_control: TimeControl | None
    clock: ClockState | None
    ended_on_time: bool


def describe(service: GameService, game: Game) -> GameResponse:
    board = service.get_board(game.id)
    replay = chess.Board()
    moves = [replay.san_and_push(move) for move in board.move_stack]
    return GameResponse(
        id=game.id,
        mode=game.mode,
        player_color=game.player_color,
        opponent_elo=game.opponent_elo,
        status=game.status,
        result=game.result,
        accuracy=game.accuracy,
        performance_rating=game.performance_rating,
        fen=board.fen(),
        moves=moves,
        side_to_move=PlayerColor.WHITE if board.turn == chess.WHITE else PlayerColor.BLACK,
        legal_moves=(
            [] if game.status is GameStatus.FINISHED else [m.uci() for m in board.legal_moves]
        ),
        time_control=game.time_control,
        clock=service.clock_state(game, board.turn),
        ended_on_time=game.ended_on_time,
    )


def game_over_event(board: chess.Board, game: Game) -> GameOverEvent:
    assert game.result is not None
    outcome = board.outcome(claim_draw=True)
    reason = outcome.termination.name.lower() if outcome else "resignation"
    if game.ended_on_time:
        reason = "timeout"
    return GameOverEvent(result=game.result.value, reason=reason)


def describe_snapshot(service: GameService, game_id: int) -> SnapshotEvent:
    board = service.get_board(game_id)
    fen, turn = board.fen(), board.turn
    last_move = board.san(board.pop()) if board.move_stack else None
    game = service.get_game(game_id)
    return SnapshotEvent(
        fen=fen,
        side_to_move=PlayerColor.WHITE if turn == chess.WHITE else PlayerColor.BLACK,
        status=game.status,
        last_move=last_move,
        clock=service.clock_state(game, turn),
    )


def publish_after_move(
    service: GameService, events: GameEvents, game: Game, plies_before: int
) -> None:
    board = service.get_board(game.id)
    if len(board.move_stack) == plies_before + 2:  # the player's move and the opponent's reply
        reply = board.pop()
        events.publish(
            game.id,
            OpponentMoveEvent(
                uci=reply.uci(),
                san=board.san(reply),
                fen=_after(board, reply),
                clock=service.clock_state(game, not board.turn),
            ),
        )
        board.push(reply)
    if game.status is GameStatus.FINISHED:
        events.publish(game.id, game_over_event(board, game))


def _after(board: chess.Board, move: chess.Move) -> str:
    board.push(move)
    fen = board.fen()
    board.pop()
    return fen


router = APIRouter(prefix="/games", tags=["games"])


@router.post("", status_code=201)
async def create_game(body: CreateGameRequest, service: Service) -> GameResponse:
    game = await service.create_game(
        DEFAULT_PLAYER_ID, body.opponent_elo, body.color, body.mode, body.time_control
    )
    return describe(service, game)


@router.get("")
def list_games(service: Service, status: GameStatus | None = None) -> list[GameResponse]:
    return [describe(service, game) for game in service.list_games(status)]


@router.get("/{game_id}")
async def get_game(game_id: int, service: Service) -> GameResponse:
    # Server-authoritative clock: a read must reflect a flag that happened while nobody watched.
    return describe(service, await service.check_flag(game_id))


@router.post("/{game_id}/moves")
async def play_move(
    game_id: int, body: MoveRequest, service: Service, events: Events, coach: Coach
) -> GameResponse:
    plies_before = len(service.get_board(game_id).move_stack)
    game = await service.play_move(game_id, body.uci)
    if game.status is GameStatus.FINISHED:
        await coach.wait_for_game(
            game_id
        )  # the final comment must reach the stream before it closes
    publish_after_move(service, events, game, plies_before)
    return describe(service, game)


@router.post("/{game_id}/resign")
async def resign(game_id: int, service: Service, events: Events, coach: Coach) -> GameResponse:
    game = service.resign(game_id)
    await coach.wait_for_game(game_id)
    events.publish(game_id, game_over_event(service.get_board(game_id), game))
    return describe(service, game)


@router.get("/{game_id}/pgn", response_class=PlainTextResponse)
def get_pgn(game_id: int, service: Service) -> str:
    return service.get_pgn(game_id)


class CoachMessageResponse(BaseModel):
    ply: int = Field(description="The 1-based ply of the move the message refers to")
    role: CoachRole
    kind: CoachMessageKind
    text: str
    classification: str | None = Field(description="The move's label, null if it was not scored")
    phase: GamePhase = Field(description="The game phase of the position before the move")
    created_at: datetime


@router.get("/{game_id}/coach/messages")
def coach_messages(
    game_id: Annotated[int, Depends(existing_game_id)], request: Request
) -> list[CoachMessageResponse]:
    """The persisted conversation in order, so a reloaded panel matches the live stream."""
    engine: Engine = request.app.state.engine
    thresholds = request.app.state.settings.thresholds.phase
    with Session(engine) as session:
        rows = session.execute(
            select(CoachMessage, Move.classification, Move.fen_before)
            .outerjoin(
                Move, (Move.game_id == CoachMessage.game_id) & (Move.ply == CoachMessage.ply)
            )
            .where(CoachMessage.game_id == game_id)
            .order_by(CoachMessage.id)
        ).all()
    return [
        CoachMessageResponse(
            ply=message.ply,
            role=message.role,
            kind=message.kind,
            text=message.text,
            classification=classification,
            phase=detect_phase(chess.Board(fen_before), thresholds),
            created_at=message.created_at,
        )
        for message, classification, fen_before in rows
    ]


@router.get("/{game_id}/coach/stream", response_class=EventSourceResponse)
async def coach_stream(
    game_id: Annotated[int, Depends(existing_game_id)], service: Service, events: Events
) -> AsyncIterator[ServerSentEvent]:
    with events.subscribe(game_id) as queue:
        snapshot = describe_snapshot(service, game_id)
        yield ServerSentEvent(data=snapshot, event=snapshot.type)
        if snapshot.status is GameStatus.FINISHED:
            return
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=CLOCK_POLL_SECONDS)
            except TimeoutError:
                game = await service.check_flag(game_id)
                if game.status is not GameStatus.FINISHED:
                    continue
                event = game_over_event(service.get_board(game_id), game)
            yield ServerSentEvent(data=event, event=event.type)
            if isinstance(event, GameOverEvent):
                return
