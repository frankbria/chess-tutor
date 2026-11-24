"""Home-screen data: the player's last game with its recap, and the last new-game choice."""

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from chess_tutor.games.service import ColorChoice
from chess_tutor.models import (
    Game,
    GameMode,
    GameResult,
    GameStatus,
    GameSummary,
    PlayerColor,
    PlayerSetting,
    TimeControl,
)

DEFAULT_PLAYER_ID = 1  # shortcut: single-user app, no auth yet; replace with the signed-in player.
LAST_MODE_KEY = "last_mode"
LAST_COLOR_KEY = "last_color"
LAST_OPPONENT_ELO_KEY = "last_opponent_elo"
LAST_TIME_CONTROL_KEY = "last_time_control"  # empty string: untimed

router = APIRouter(prefix="/player", tags=["player"])


class LastChoice(BaseModel):
    mode: GameMode | None
    color: ColorChoice | None
    opponent_elo: int | None
    time_control: TimeControl | None


class LastGame(BaseModel):
    game_id: int
    mode: GameMode
    player_color: PlayerColor
    opponent_elo: int
    result: GameResult | None
    accuracy: float | None
    summary: str | None


def remember_choice(
    session: Session,
    mode: GameMode,
    color: ColorChoice,
    opponent_elo: int,
    time_control: TimeControl | None,
) -> None:
    for key, value in (
        (LAST_MODE_KEY, mode.value),
        (LAST_COLOR_KEY, color.value),
        (LAST_OPPONENT_ELO_KEY, str(opponent_elo)),
        (LAST_TIME_CONTROL_KEY, time_control.value if time_control else ""),
    ):
        session.merge(PlayerSetting(player_id=DEFAULT_PLAYER_ID, key=key, value=value))
    session.commit()


@router.get("/last-choice")
def get_last_choice(request: Request) -> LastChoice:
    with Session(request.app.state.engine) as session:
        stored = {
            row.key: row.value
            for row in session.scalars(
                select(PlayerSetting).where(PlayerSetting.player_id == DEFAULT_PLAYER_ID)
            )
        }
    mode = stored.get(LAST_MODE_KEY)
    color = stored.get(LAST_COLOR_KEY)
    elo = stored.get(LAST_OPPONENT_ELO_KEY)
    time_control = stored.get(LAST_TIME_CONTROL_KEY)
    return LastChoice(
        mode=GameMode(mode) if mode in set(GameMode) else None,
        color=ColorChoice(color) if color in set(ColorChoice) else None,
        opponent_elo=int(elo) if elo is not None and elo.isdigit() else None,
        time_control=TimeControl(time_control) if time_control in set(TimeControl) else None,
    )


@router.get("/last-game")
def get_last_game(request: Request) -> LastGame | None:
    with Session(request.app.state.engine) as session:
        game = session.scalars(
            select(Game)
            .where(Game.player_id == DEFAULT_PLAYER_ID, Game.status == GameStatus.FINISHED)
            .order_by(Game.ended_at.desc(), Game.id.desc())
            .limit(1)
        ).one_or_none()
        if game is None:
            return None
        summary = session.scalar(select(GameSummary.summary).where(GameSummary.game_id == game.id))
        return LastGame(
            game_id=game.id,
            mode=game.mode,
            player_color=game.player_color,
            opponent_elo=game.opponent_elo,
            result=game.result,
            accuracy=game.accuracy,
            summary=summary,
        )
