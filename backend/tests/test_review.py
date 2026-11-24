import json

import chess
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from scripted_opponent import ScriptedOpponent
from sqlalchemy.orm import Session
from test_passive_coach import OPPONENT_REPLIES, PLAYER_MOVES

from chess_tutor.engines.stockfish import Budget, StockfishPool
from chess_tutor.main import create_app
from chess_tutor.models import (
    CoachMessage,
    CoachMessageKind,
    CoachMessageSource,
    CoachRole,
    Game,
    GameSummary,
    Move,
)
from chess_tutor.review.report import MAX_HIGHLIGHTS, MAX_SWINGS, pick_key_moments
from chess_tutor.settings import Settings


def play_game(
    app: FastAPI, api: TestClient, moves: int = 4, finish: bool = True, mode: str = "passive"
) -> int:
    game_id: int = api.post(
        "/games", json={"mode": mode, "color": "white", "opponent_elo": 1200}
    ).json()["id"]
    for uci in PLAYER_MOVES[:moves]:
        assert api.post(f"/games/{game_id}/moves", json={"uci": uci}).status_code == 200
    assert api.portal is not None
    api.portal.call(app.state.passive_coach.wait_until_idle)  # classification runs in background
    if finish:
        assert api.post(f"/games/{game_id}/resign").status_code == 200
        api.portal.call(app.state.passive_coach.wait_until_idle)
    return game_id


def fake_move(ply: int, classification: str, before: float, after: float) -> Move:
    return Move(
        game_id=1, ply=ply, san="x", uci="a2a3", fen_before="", classification=classification,
        eval_before=before, eval_after=after,
    )  # fmt: skip


def test_an_unfinished_game_has_no_review(settings: Settings) -> None:
    app = create_app(settings, ScriptedOpponent(*OPPONENT_REPLIES), None)
    with TestClient(app) as api:
        game_id = play_game(app, api, moves=1, finish=False)
        response = api.get(f"/games/{game_id}/review")

    assert response.status_code == 409


def test_a_game_without_a_stored_summary_still_has_a_review(settings: Settings) -> None:
    app = create_app(settings, ScriptedOpponent(*OPPONENT_REPLIES), None)
    with TestClient(app) as api:
        game_id = play_game(app, api)
        with Session(app.state.engine) as session:
            session.query(GameSummary).filter_by(game_id=game_id).delete()
            session.commit()
        body = api.get(f"/games/{game_id}/review").json()

    assert body["summary"] is None and body["key_moments"]


def test_a_black_player_gets_their_own_blunder_and_its_best_move(settings: Settings) -> None:
    app = create_app(settings, ScriptedOpponent("e4", "Nf3", "Bc4", "Nxh4"), None)
    with TestClient(app) as api:
        game_id = api.post(
            "/games", json={"mode": "passive", "color": "black", "opponent_elo": 1200}
        ).json()["id"]
        for uci in ("e7e5", "b8c6", "d8h4"):  # the last hangs the queen to Nf3
            assert api.post(f"/games/{game_id}/moves", json={"uci": uci}).status_code == 200
        assert api.portal is not None
        api.portal.call(app.state.passive_coach.wait_until_idle)
        api.post(f"/games/{game_id}/resign")
        api.portal.call(app.state.passive_coach.wait_until_idle)
        body = api.get(f"/games/{game_id}/review").json()
        with Session(app.state.engine) as session:
            fen_before = session.query(Move).filter_by(game_id=game_id, ply=6).one().fen_before
        pool: StockfishPool = app.state.stockfish_pool
        top_lines = api.portal.call(pool.analyse, chess.Board(fen_before), Budget.DEEP, 3)

    [moment] = body["key_moments"]
    assert (moment["ply"], moment["san"], moment["classification"]) == (6, "Qh4", "blunder")
    # A quiet position has near-equal best moves, so any of the engine's top three will do.
    assert moment["best_move_uci"] in {line.pv[0].uci() for line in top_lines}
    assert moment["best_move_uci"] != "d8h4"


def test_the_review_carries_the_openings_and_the_game_response_agrees(settings: Settings) -> None:
    app = create_app(settings, ScriptedOpponent(*OPPONENT_REPLIES), None)
    with TestClient(app) as api:
        game_id = play_game(app, api)
        review = api.get(f"/games/{game_id}/review").json()
        game = api.get(f"/games/{game_id}").json()

    assert len(review["opening_line"]) == 8
    assert review["opening_line"][0]["eco"] == "B00"
    assert review["opening_line"] == game["opening_line"]
    assert review["book_exit"] == game["book_exit"]


def test_an_unknown_game_is_a_404(settings: Settings) -> None:
    with TestClient(create_app(settings, ScriptedOpponent(*OPPONENT_REPLIES), None)) as api:
        assert api.get("/games/999/review").status_code == 404


def test_the_review_reveals_the_best_move_at_a_blunder(settings: Settings) -> None:
    app = create_app(settings, ScriptedOpponent(*OPPONENT_REPLIES), None)
    with TestClient(app) as api:
        game_id = play_game(app, api)
        body = api.get(f"/games/{game_id}/review").json()
        with Session(app.state.engine) as session:
            blunder = session.query(Move).filter_by(game_id=game_id, ply=7).one()
            fen_before = blunder.fen_before
        pool: StockfishPool = app.state.stockfish_pool
        assert api.portal is not None
        [best] = api.portal.call(pool.analyse, chess.Board(fen_before), Budget.DEEP)

    [moment] = [m for m in body["key_moments"] if m["ply"] == 7]
    board = chess.Board(fen_before)
    assert moment["classification"] == "blunder" and moment["san"] == "Nxe5"
    assert moment["best_move_uci"] == best.pv[0].uci() != moment["uci"]
    assert moment["best_move_san"] == board.san(best.pv[0])
    assert moment["win_probability_before"] > moment["win_probability_after"]
    assert body["accuracy"] is not None and body["performance_rating"] is not None
    assert body["summary"]["source"] == "template" and "you lost" in body["summary"]["summary"]


def test_the_review_carries_the_players_win_probability_after_each_scored_move(
    settings: Settings,
) -> None:
    app = create_app(settings, ScriptedOpponent(*OPPONENT_REPLIES), None)
    with TestClient(app) as api:
        game_id = play_game(app, api)
        body = api.get(f"/games/{game_id}/review").json()
        with Session(app.state.engine) as session:
            scored = [
                (move.ply, move.eval_after)
                for move in session.query(Move).filter_by(game_id=game_id).order_by(Move.ply)
                if move.eval_after is not None
            ]

    series = [(point["ply"], point["win_probability"]) for point in body["win_probabilities"]]
    assert series == scored and len(series) == 4


def test_a_regular_game_is_scored_on_first_review_and_the_scores_are_kept(
    settings: Settings,
) -> None:
    app = create_app(settings, ScriptedOpponent(*OPPONENT_REPLIES), None)
    with TestClient(app) as api:
        game_id = play_game(app, api, mode="regular")
        with Session(app.state.engine) as session:
            assert session.query(Move).filter(Move.eval_after.is_not(None)).count() == 0
        body = api.get(f"/games/{game_id}/review").json()
        with Session(app.state.engine) as session:
            stored = [
                (move.ply, move.eval_after)
                for move in session.query(Move).filter_by(game_id=game_id).order_by(Move.ply)
                if move.eval_after is not None
            ]
        again = api.get(f"/games/{game_id}/review").json()

    assert [point["ply"] for point in body["win_probabilities"]] == [1, 3, 5, 7]
    assert [m["ply"] for m in body["key_moments"]] == [7]
    assert stored == [(p["ply"], p["win_probability"]) for p in body["win_probabilities"]]
    assert again["win_probabilities"] == body["win_probabilities"]
    assert body["accuracy"] is not None and body["performance_rating"] is not None
    with Session(app.state.engine) as session:
        game = session.get_one(Game, game_id)
        assert (game.accuracy, game.performance_rating) == (
            body["accuracy"],
            body["performance_rating"],
        )


def test_the_stored_summary_reasoning_and_concepts_are_returned(settings: Settings) -> None:
    app = create_app(settings, ScriptedOpponent(*OPPONENT_REPLIES), None)
    with TestClient(app) as api:
        game_id = play_game(app, api)
        with Session(app.state.engine) as session:
            stored = session.query(GameSummary).filter_by(game_id=game_id).one()
            stored.summary = "You lost a piece."
            stored.key_moments = json.dumps(["Move 4 gave a piece away."])
            stored.concepts = json.dumps(["hanging_piece"])
            for ply, role, kind, text in (
                (7, CoachRole.COACH, CoachMessageKind.QUESTION, "What does Black threaten?"),
                (7, CoachRole.PLAYER, CoachMessageKind.ANSWER, "Nothing much."),
                (9, CoachRole.COACH, CoachMessageKind.COMMENT, "A comment on another move."),
                (7, CoachRole.COACH, CoachMessageKind.COMMENT, "Look again at the knight."),
            ):
                session.add(
                    CoachMessage(
                        game_id=game_id, ply=ply, role=role, kind=kind, text=text, fact_ids="[]",
                        source=CoachMessageSource.TEMPLATE, concepts="[]",
                    )  # fmt: skip
                )
            session.commit()
        body = api.get(f"/games/{game_id}/review").json()

    assert body["summary"] == {
        "summary": "You lost a piece.",
        "key_moments": ["Move 4 gave a piece away."],
        "concepts": ["hanging_piece"],
        "source": "template",
    }
    assert body["reasoning"] == [
        {"ply": 7, "answer": "Nothing much.", "coach_reply": "Look again at the knight."}
    ]
    assert body["concepts"] and {"ply", "concept", "source"} == set(body["concepts"][0])


def test_key_moments_are_the_biggest_swings_and_the_highlights_in_play_order() -> None:
    swings = [fake_move(2 * n + 1, "mistake", 60, 60 - 5 * n) for n in range(1, MAX_SWINGS + 3)]
    highlights = [fake_move(100 + 2 * n, "great", 50, 50) for n in range(MAX_HIGHLIGHTS + 1)]
    minor = fake_move(51, "good", 50, 49)

    chosen = pick_key_moments([*swings, minor, *highlights])

    plies = [move.ply for move in chosen]
    assert plies == sorted(plies) and len(chosen) == MAX_SWINGS + MAX_HIGHLIGHTS
    assert swings[-1] in chosen and swings[0] not in chosen and minor not in chosen
    assert highlights[0] in chosen and highlights[-1] not in chosen


@pytest.mark.parametrize("classification", ["best", "good"])
def test_clean_play_has_no_key_moments(classification: str) -> None:
    assert pick_key_moments([fake_move(1, classification, 50, 50)]) == []


def stored_message(role: CoachRole, kind: CoachMessageKind, text: str) -> CoachMessage:
    return CoachMessage(
        game_id=1, ply=7, role=role, kind=kind, text=text, fact_ids="[]",
        source=CoachMessageSource.TEMPLATE, concepts="[]",
    )  # fmt: skip


def test_a_review_reply_never_pairs_with_an_earlier_unanswered_answer() -> None:
    from chess_tutor.review.report import _reasoning

    unanswered = [
        stored_message(CoachRole.PLAYER, CoachMessageKind.ANSWER, "my idea"),
        stored_message(CoachRole.PLAYER, CoachMessageKind.ASKED, "what was best?"),
        stored_message(CoachRole.COACH, CoachMessageKind.ANSWER, "d4 was best."),
    ]
    answered = [
        stored_message(CoachRole.PLAYER, CoachMessageKind.ANSWER, "my idea"),
        stored_message(CoachRole.COACH, CoachMessageKind.ANSWER, "I see the idea."),
        stored_message(CoachRole.PLAYER, CoachMessageKind.ASKED, "what was best?"),
        stored_message(CoachRole.COACH, CoachMessageKind.ANSWER, "d4 was best."),
    ]

    assert [(e.answer, e.coach_reply) for e in _reasoning(unanswered)] == [("my idea", None)]
    assert [(e.answer, e.coach_reply) for e in _reasoning(answered)] == [
        ("my idea", "I see the idea.")
    ]
