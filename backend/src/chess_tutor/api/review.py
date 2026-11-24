"""`GET /games/{id}/review`: the post-game review of a finished game, best moves revealed."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from chess_tutor.engines.stockfish import StockfishPool
from chess_tutor.models import ConceptSource, Game, GameStatus
from chess_tutor.review.report import build_review

router = APIRouter(prefix="/games", tags=["review"])


class KeyMomentResponse(BaseModel):
    ply: int
    move_number: int
    san: str
    uci: str
    fen_before: str
    classification: str
    win_probability_before: float | None
    win_probability_after: float | None
    best_move_uci: str
    best_move_san: str


class ReasoningResponse(BaseModel):
    ply: int
    answer: str
    coach_reply: str | None


class SummaryResponse(BaseModel):
    summary: str
    key_moments: list[str]
    concepts: list[str]
    source: str


class ConceptHitResponse(BaseModel):
    ply: int
    concept: str
    source: ConceptSource


class ReviewResponse(BaseModel):
    accuracy: float | None
    performance_rating: float | None
    key_moments: list[KeyMomentResponse]
    reasoning: list[ReasoningResponse]
    summary: SummaryResponse | None = Field(description="Null when no summary was stored")
    concepts: list[ConceptHitResponse]


@router.get("/{game_id}/review")
async def get_review(game_id: int, request: Request) -> ReviewResponse:
    engine: Engine = request.app.state.engine
    pool: StockfishPool | None = getattr(request.app.state, "stockfish_pool", None)
    if pool is None:
        raise HTTPException(503, "the analysis engine is not running")
    with Session(engine) as session:
        game = session.get(Game, game_id)
        if game is None:
            raise HTTPException(404, f"game {game_id} does not exist")
        session.expunge(game)
    if game.status is not GameStatus.FINISHED:
        raise HTTPException(409, "the review is available once the game is over")
    review = await build_review(engine, pool, game)
    return ReviewResponse.model_validate(review, from_attributes=True)
