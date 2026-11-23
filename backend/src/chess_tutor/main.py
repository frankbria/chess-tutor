import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from chess_tutor.api.events import GameEvents
from chess_tutor.api.games import register_game_errors
from chess_tutor.api.games import router as games_router
from chess_tutor.database import create_database_engine, run_migrations, seed_default_player
from chess_tutor.engines.human_moves import HumanMovePredictor
from chess_tutor.engines.opponent import Maia3Opponent, OpponentEngine
from chess_tutor.engines.think_time import OpponentPacing
from chess_tutor.games.service import GameService
from chess_tutor.models import Player, RatingHistory
from chess_tutor.openings.book import opening_index
from chess_tutor.settings import Settings


class PlayerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    display_name: str
    rating: float
    rating_deviation: float


class RatingHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    game_id: int
    rating_before: float
    rating_after: float
    deviation_before: float
    deviation_after: float
    weight: float
    created_at: datetime


def create_app(settings: Settings | None = None, opponent: OpponentEngine | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        run_migrations(settings)
        await asyncio.to_thread(opening_index)  # parse the book before the first move needs it
        engine = create_database_engine(settings)
        seed_default_player(engine)
        app.state.engine = engine
        app.state.game_events = GameEvents()
        game_opponent = opponent
        if game_opponent is None and settings.maia_enabled:
            game_opponent = Maia3Opponent.from_settings(settings)
        if game_opponent is None:
            yield
            engine.dispose()
            return
        predictor = HumanMovePredictor.from_settings(settings) if settings.maia_enabled else None
        try:
            await game_opponent.start()
            if predictor is not None:
                await predictor.start()
        except Exception:
            await game_opponent.close()
            if predictor is not None:
                await predictor.close()
            engine.dispose()
            raise
        pacing = None if predictor is None else OpponentPacing(settings, predictor)
        app.state.game_service = GameService(engine, game_opponent, pacing, settings.thresholds)
        try:
            yield
        finally:
            if predictor is not None:
                await predictor.close()
            await game_opponent.close()
            engine.dispose()

    app = FastAPI(title="Chess Tutor", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=settings.frontend_origins)
    app.include_router(games_router)
    register_game_errors(app)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/player", response_model=PlayerResponse)
    def get_player(request: Request) -> Player:
        with Session(request.app.state.engine) as session:
            return session.scalars(select(Player).order_by(Player.id).limit(1)).one()

    @app.get("/player/rating-history", response_model=list[RatingHistoryResponse])
    def get_rating_history(request: Request) -> list[RatingHistory]:
        with Session(request.app.state.engine) as session:
            player = session.scalars(select(Player).order_by(Player.id).limit(1)).one()
            return list(
                session.scalars(
                    select(RatingHistory)
                    .where(RatingHistory.player_id == player.id)
                    .order_by(RatingHistory.id)
                )
            )

    return app
