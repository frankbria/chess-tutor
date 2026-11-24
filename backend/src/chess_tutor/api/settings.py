"""`GET/PUT /settings`: the player's preferences plus the coach's LLM spend against the ceiling."""

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from chess_tutor.models import Game, GameMode, PlayerSetting

DEFAULT_PLAYER_ID = 1  # shortcut: single-user app, no auth yet; replace with the signed-in player.
DEFAULT_MONTHLY_COST_CEILING = 10.0  # US dollars; the spec expects ~$3-8 a month
BUDGET_ALERT_FRACTION = 0.8
RECENT_GAME_COUNT = 10

CoachVerbosity = Literal["brief", "normal", "detailed"]
MaiaModel = Literal["3m", "5m", "23m", "79m"]

router = APIRouter()


class Preferences(BaseModel):
    coach_verbosity: CoachVerbosity
    default_mode: GameMode
    maia_model: MaiaModel
    monthly_cost_ceiling: float = Field(gt=0)


class GameCost(BaseModel):
    game_id: int
    started_at: datetime
    llm_cost: float


class SettingsResponse(BaseModel):
    preferences: Preferences
    monthly_cost: float
    budget_alert: bool
    recent_games: list[GameCost]


def load_preferences(session: Session, maia_model: MaiaModel) -> Preferences:
    rows = session.scalars(
        select(PlayerSetting).where(PlayerSetting.player_id == DEFAULT_PLAYER_ID)
    )
    stored = {row.key: row.value for row in rows}
    values: dict[str, object] = {
        "coach_verbosity": "normal",
        "default_mode": GameMode.PASSIVE,
        "maia_model": maia_model,
        "monthly_cost_ceiling": DEFAULT_MONTHLY_COST_CEILING,
    }
    for key, value in stored.items():
        if key not in values:
            continue
        try:
            Preferences.model_validate({**values, key: value})
        except ValidationError:
            continue  # a stale or hand-edited row must not lock the user out of the form
        values[key] = value
    return Preferences.model_validate(values)


def stored_maia_model(engine: Engine, fallback: MaiaModel) -> MaiaModel:
    with Session(engine) as session:
        return load_preferences(session, fallback).maia_model


def build_response(session: Session, maia_model: MaiaModel) -> SettingsResponse:
    preferences = load_preferences(session, maia_model)
    now = datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    monthly_cost = float(
        session.scalar(
            select(func.coalesce(func.sum(Game.llm_cost), 0.0)).where(
                Game.player_id == DEFAULT_PLAYER_ID, Game.started_at >= month_start
            )
        )
        or 0.0
    )
    recent = session.scalars(
        select(Game)
        .where(Game.player_id == DEFAULT_PLAYER_ID)
        .order_by(Game.id.desc())
        .limit(RECENT_GAME_COUNT)
    )
    return SettingsResponse(
        preferences=preferences,
        monthly_cost=monthly_cost,
        budget_alert=monthly_cost >= BUDGET_ALERT_FRACTION * preferences.monthly_cost_ceiling,
        recent_games=[
            GameCost(game_id=game.id, started_at=game.started_at, llm_cost=game.llm_cost)
            for game in recent
        ],
    )


@router.get("/settings", response_model=SettingsResponse)
def get_settings(request: Request) -> SettingsResponse:
    with Session(request.app.state.engine) as session:
        return build_response(session, request.app.state.settings.maia_model)


@router.put("/settings", response_model=SettingsResponse)
def put_settings(preferences: Preferences, request: Request) -> SettingsResponse:
    with Session(request.app.state.engine) as session:
        for key, value in preferences.model_dump(mode="json").items():
            session.merge(PlayerSetting(player_id=DEFAULT_PLAYER_ID, key=key, value=str(value)))
        session.commit()
        return build_response(session, request.app.state.settings.maia_model)
