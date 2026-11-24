"""`GET /games/{id}/review`: the post-game review of a finished game, best moves revealed.

`POST /games/{id}/review/questions` `{ply, text}` asks the coach about the position after `ply`
(0 is the start), with the best moves revealed; the reply is stored and returned.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from chess_tutor.api.games import get_passive_coach
from chess_tutor.coach.passive import PassiveCoach
from chess_tutor.engines.stockfish import StockfishPool
from chess_tutor.models import ConceptSource, Game, GameStatus
from chess_tutor.review.report import build_review

router = APIRouter(prefix="/games", tags=["review"])


class PlyWinProbabilityResponse(BaseModel):
    ply: int
    win_probability: float


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
    win_probabilities: list[PlyWinProbabilityResponse]
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
    review = await build_review(
        engine, pool, game, request.app.state.settings.thresholds.classification
    )
    return ReviewResponse.model_validate(review, from_attributes=True)


class ReviewQuestionRequest(BaseModel):
    ply: int = Field(ge=0, description="The position after this ply; 0 is the start")
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class ReviewAnswerResponse(BaseModel):
    text: str
    source: str = Field(description="`llm`, `llm_regenerated` or `template`")


@router.post("/{game_id}/review/questions")
async def ask_in_review(
    game_id: int,
    body: ReviewQuestionRequest,
    request: Request,
    coach: Annotated[PassiveCoach, Depends(get_passive_coach)],
) -> ReviewAnswerResponse:
    engine: Engine = request.app.state.engine
    with Session(engine) as session:
        game = session.get(Game, game_id)
        if game is None:
            raise HTTPException(404, f"game {game_id} does not exist")
        if game.status is not GameStatus.FINISHED:
            raise HTTPException(409, "review questions are available once the game is over")
    reply = await coach.ask_about_position(game_id, body.ply, body.text)
    return ReviewAnswerResponse(text=reply.text, source=reply.source.value)
