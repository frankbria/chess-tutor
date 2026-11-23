"""Game REST API and the per-game SSE stream.

SSE `GET /games/{id}/coach/stream` emits one JSON object per event, with the SSE `event:` name
equal to the object's `type`:

- `opponent_move`: `{"type", "uci", "san", "fen"}`, the opponent's move and the resulting FEN.
- `game_over`: `{"type", "result", "reason"}`; `result` is `1-0`, `0-1` or `1/2-1/2`, `reason` is
  `checkmate`, `stalemate`, `insufficient_material`, `fifty_moves`, `threefold_repetition`, etc.,
  or `resignation`.

Events are published after the service call that caused them, so subscribe before moving.
"""

from collections.abc import AsyncIterator
from typing import Annotated

import chess
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, Field

from chess_tutor.api.events import GameEvents, GameOverEvent, OpponentMoveEvent
from chess_tutor.games.service import (
    ColorChoice,
    GameNotActiveError,
    GameNotFoundError,
    GameService,
    IllegalMoveError,
    InvalidGameSettingsError,
    NotPlayersTurnError,
    OpponentUnavailableError,
)
from chess_tutor.models import Game, GameMode, GameResult, GameStatus, PlayerColor

DEFAULT_PLAYER_ID = 1  # shortcut: single-user app, no auth yet; replace with the signed-in player.

ERROR_STATUS: dict[type[Exception], int] = {
    GameNotFoundError: 404,
    IllegalMoveError: 422,
    InvalidGameSettingsError: 422,
    NotPlayersTurnError: 409,
    GameNotActiveError: 409,
    OpponentUnavailableError: 503,
}


async def game_error_handler(_request: Request, error: Exception) -> JSONResponse:
    return JSONResponse({"detail": str(error)}, status_code=ERROR_STATUS[type(error)])


def register_game_errors(app: FastAPI) -> None:
    for error_type in ERROR_STATUS:
        app.add_exception_handler(error_type, game_error_handler)


def get_game_service(request: Request) -> GameService:
    service: GameService | None = getattr(request.app.state, "game_service", None)
    if service is None:
        raise HTTPException(503, "the opponent engine is not running")
    return service


def get_game_events(request: Request) -> GameEvents:
    events: GameEvents = request.app.state.game_events
    return events


Service = Annotated[GameService, Depends(get_game_service)]
Events = Annotated[GameEvents, Depends(get_game_events)]


def existing_game_id(game_id: int, service: Service) -> int:
    """Checked before the response starts, because errors raised mid-stream can't become a 404."""
    service.get_game(game_id)
    return game_id


class CreateGameRequest(BaseModel):
    mode: GameMode = GameMode.REGULAR
    color: ColorChoice = ColorChoice.RANDOM
    opponent_elo: int = Field(description="Opponent strength, validated by the service")


class MoveRequest(BaseModel):
    uci: str = Field(examples=["e2e4"])


class GameResponse(BaseModel):
    id: int
    mode: GameMode
    player_color: PlayerColor
    opponent_elo: int
    status: GameStatus
    result: GameResult | None
    fen: str
    moves: list[str] = Field(description="Moves in SAN, in play order")
    side_to_move: PlayerColor
    legal_moves: list[str] = Field(description="Legal moves in UCI, empty once the game is over")


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
        fen=board.fen(),
        moves=moves,
        side_to_move=PlayerColor.WHITE if board.turn == chess.WHITE else PlayerColor.BLACK,
        legal_moves=(
            [] if game.status is GameStatus.FINISHED else [m.uci() for m in board.legal_moves]
        ),
    )


def game_over_event(board: chess.Board, game: Game) -> GameOverEvent:
    assert game.result is not None
    outcome = board.outcome(claim_draw=True)
    reason = outcome.termination.name.lower() if outcome else "resignation"
    return GameOverEvent(result=game.result.value, reason=reason)


def publish_after_move(
    service: GameService, events: GameEvents, game: Game, plies_before: int
) -> None:
    board = service.get_board(game.id)
    if len(board.move_stack) == plies_before + 2:  # the player's move and the opponent's reply
        reply = board.pop()
        events.publish(
            game.id,
            OpponentMoveEvent(uci=reply.uci(), san=board.san(reply), fen=_after(board, reply)),
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
    game = await service.create_game(DEFAULT_PLAYER_ID, body.opponent_elo, body.color, body.mode)
    return describe(service, game)


@router.get("")
def list_games(service: Service, status: GameStatus | None = None) -> list[GameResponse]:
    return [describe(service, game) for game in service.list_games(status)]


@router.get("/{game_id}")
def get_game(game_id: int, service: Service) -> GameResponse:
    return describe(service, service.get_game(game_id))


@router.post("/{game_id}/moves")
async def play_move(
    game_id: int, body: MoveRequest, service: Service, events: Events
) -> GameResponse:
    plies_before = len(service.get_board(game_id).move_stack)
    game = await service.play_move(game_id, body.uci)
    publish_after_move(service, events, game, plies_before)
    return describe(service, game)


@router.post("/{game_id}/resign")
def resign(game_id: int, service: Service, events: Events) -> GameResponse:
    game = service.resign(game_id)
    events.publish(game_id, game_over_event(service.get_board(game_id), game))
    return describe(service, game)


@router.get("/{game_id}/pgn", response_class=PlainTextResponse)
def get_pgn(game_id: int, service: Service) -> str:
    return service.get_pgn(game_id)


@router.get("/{game_id}/coach/stream", response_class=EventSourceResponse)
async def coach_stream(
    game_id: Annotated[int, Depends(existing_game_id)], events: Events
) -> AsyncIterator[ServerSentEvent]:
    with events.subscribe(game_id) as queue:
        yield ServerSentEvent(comment="subscribed")
        while True:
            event = await queue.get()
            yield ServerSentEvent(data=event, event=event.type)
