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
from chess_tutor.coach.llm import LanguageModel, create_language_model
from chess_tutor.coach.passive import PassiveCoach
from chess_tutor.concepts.taxonomy import CONCEPTS, Concept
from chess_tutor.database import create_database_engine, run_migrations, seed_default_player
from chess_tutor.engines.human_moves import HumanMovePredictor
from chess_tutor.engines.opponent import OpponentEngine
from chess_tutor.engines.routing import build_opponent
from chess_tutor.engines.steering import SteeringOpponent, focus_concepts_of_first_player
from chess_tutor.engines.stockfish import StockfishPool
from chess_tutor.engines.think_time import OpponentPacing
from chess_tutor.games.service import GameService
from chess_tutor.models import ConceptState, ConceptStatus, Game, Player, RatingHistory
from chess_tutor.openings.book import opening_index
from chess_tutor.settings import Settings


class PlayerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    display_name: str
    rating: float
    rating_deviation: float


class RatingHistoryResponse(BaseModel):
    game_id: int
    rating_before: float
    rating_after: float
    deviation_before: float
    deviation_after: float
    weight: float
    created_at: datetime
    accuracy: float | None
    performance_rating: float | None


class ConceptStateResponse(BaseModel):
    concept: str
    label: str
    state: ConceptStatus
    count: int
    recency_score: float
    practice_themes: list[str]


CONCEPT_STATE_ORDER = (
    ConceptStatus.FOCUS,
    ConceptStatus.RECURRING,
    ConceptStatus.IMPROVING,
    ConceptStatus.NEW,
    ConceptStatus.MASTERED,
)


def create_app(
    settings: Settings | None = None,
    opponent: OpponentEngine | None = None,
    language_model: LanguageModel | None = None,
) -> FastAPI:
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
            game_opponent = build_opponent(settings)
        if game_opponent is None:
            yield
            engine.dispose()
            return
        predictor = HumanMovePredictor.from_settings(settings) if settings.maia_enabled else None
        pool = StockfishPool(settings.stockfish_path, settings.stockfish_pool_size)  # shared
        if predictor is not None and settings.opponent_steering_enabled:
            game_opponent = SteeringOpponent(
                game_opponent,
                predictor,
                pool,
                lambda: focus_concepts_of_first_player(engine),
                settings.opponent_steering_rate,
                settings.opponent_steering_max_eval_loss_centipawns,
                max_steered_elo=settings.opponent_stockfish_above_elo,
            )
        app.state.opponent = game_opponent
        try:
            await game_opponent.start()
            if predictor is not None:
                await predictor.start()
            await pool.start()
        except Exception:
            await game_opponent.close()
            if predictor is not None:
                await predictor.close()
            await pool.close()
            engine.dispose()
            raise
        pacing = None if predictor is None else OpponentPacing(settings, predictor)
        app.state.game_service = GameService(engine, game_opponent, pacing, settings.thresholds)
        passive_coach = PassiveCoach(
            engine,
            pool,
            language_model or create_language_model(settings),
            app.state.game_events,
            settings,
        )
        app.state.passive_coach = passive_coach
        try:
            yield
        finally:
            await passive_coach.close()
            await pool.close()
            if predictor is not None:
                await predictor.close()
            await game_opponent.close()
            engine.dispose()

    app = FastAPI(title="Chess Tutor", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.frontend_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
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
    def get_rating_history(request: Request) -> list[RatingHistoryResponse]:
        with Session(request.app.state.engine) as session:
            player = session.scalars(select(Player).order_by(Player.id).limit(1)).one()
            rows = session.execute(
                select(RatingHistory, Game)
                .join(Game, Game.id == RatingHistory.game_id)
                .where(RatingHistory.player_id == player.id)
                .order_by(RatingHistory.id)
            )
            return [
                RatingHistoryResponse(
                    game_id=history.game_id,
                    rating_before=history.rating_before,
                    rating_after=history.rating_after,
                    deviation_before=history.deviation_before,
                    deviation_after=history.deviation_after,
                    weight=history.weight,
                    created_at=history.created_at,
                    accuracy=game.accuracy,
                    performance_rating=game.performance_rating,
                )
                for history, game in rows
            ]

    @app.get("/player/concepts", response_model=list[ConceptStateResponse])
    def get_concepts(request: Request) -> list[ConceptStateResponse]:
        with Session(request.app.state.engine) as session:
            player = session.scalars(select(Player).order_by(Player.id).limit(1)).one()
            rows = session.scalars(
                select(ConceptState).where(ConceptState.player_id == player.id)
            ).all()
        rows = sorted(
            rows, key=lambda row: (CONCEPT_STATE_ORDER.index(row.state), -row.recency_score)
        )
        known = {concept.value for concept in CONCEPTS}
        # A concept retired from the taxonomy keeps its stored rows; skip them, don't fail the list.
        return [
            ConceptStateResponse(
                concept=row.concept,
                label=CONCEPTS[Concept(row.concept)].label,
                state=row.state,
                count=row.count,
                recency_score=row.recency_score,
                practice_themes=list(CONCEPTS[Concept(row.concept)].practice_themes),
            )
            for row in rows
            if row.concept in known
        ]

    return app
