from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from chess_tutor.database import create_database_engine, run_migrations, seed_default_player
from chess_tutor.engines.human_moves import HumanMovePredictor
from chess_tutor.engines.opponent import Maia3Opponent
from chess_tutor.engines.think_time import OpponentPacing
from chess_tutor.games.service import GameService
from chess_tutor.models import Player
from chess_tutor.settings import Settings


class PlayerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    display_name: str
    rating: float
    rating_deviation: float


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        run_migrations(settings)
        engine = create_database_engine(settings)
        seed_default_player(engine)
        app.state.engine = engine
        if not settings.maia_enabled:
            yield
            engine.dispose()
            return
        opponent = Maia3Opponent.from_settings(settings)
        predictor = HumanMovePredictor.from_settings(settings)
        try:
            await opponent.start()
            await predictor.start()
        except BaseException:
            await opponent.close()
            await predictor.close()
            engine.dispose()
            raise
        pacing = OpponentPacing(settings, predictor)
        app.state.game_service = GameService(engine, opponent, pacing)
        try:
            yield
        finally:
            await predictor.close()
            await opponent.close()
            engine.dispose()

    app = FastAPI(title="Chess Tutor", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=settings.frontend_origins)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/player", response_model=PlayerResponse)
    def get_player(request: Request) -> Player:
        with Session(request.app.state.engine) as session:
            return session.scalars(select(Player).order_by(Player.id).limit(1)).one()

    return app
