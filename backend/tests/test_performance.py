from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from scripted_opponent import ScriptedOpponent
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from chess_tutor.analysis.classification import game_accuracy
from chess_tutor.database import create_database_engine, run_migrations, seed_default_player
from chess_tutor.games.service import ColorChoice, GameService
from chess_tutor.main import create_app
from chess_tutor.models import Game, GameMode, Move, Player
from chess_tutor.rating.performance import performance_rating, record_performance
from chess_tutor.settings import Settings

PLAYER_ID = 1
PERFECT = game_accuracy([0.0, 0.0])


@pytest.fixture
def engine(settings: Settings) -> Iterator[Engine]:
    run_migrations(settings)
    database_engine = create_database_engine(settings)
    seed_default_player(database_engine)
    yield database_engine
    database_engine.dispose()


def test_performance_is_opponent_elo_at_the_baseline_accuracy_and_moves_with_it() -> None:
    assert performance_rating(1200, 75.0) == 1200
    assert performance_rating(1200, 100.0) > 1200 > performance_rating(1200, 50.0)


def test_performance_is_clamped_to_the_elo_bounds() -> None:
    assert performance_rating(2950, 100.0) == 3000
    assert performance_rating(450, 0.0) == 400


def set_evals(engine: Engine, game_id: int, evals: dict[int, tuple[float, float]]) -> None:
    with Session(engine) as session:
        for ply, (before, after) in evals.items():
            move = session.query(Move).filter_by(game_id=game_id, ply=ply).one()
            move.eval_before, move.eval_after = before, after
        session.commit()


async def two_move_game(engine: Engine) -> int:
    service = GameService(engine, ScriptedOpponent("e5", "Nc6"))
    game = await service.create_game(PLAYER_ID, 1200, ColorChoice.WHITE)
    await service.play_move(game.id, "g1f3")
    await service.play_move(game.id, "b1c3")
    service.resign(game.id)
    return game.id


async def test_accuracy_uses_only_the_players_classified_moves(engine: Engine) -> None:
    game_id = await two_move_game(engine)
    # plies 1 and 3 are the white player's; ply 2 (the opponent's) must be ignored.
    set_evals(engine, game_id, {1: (50.0, 50.0), 2: (50.0, 5.0), 3: (60.0, 40.0)})

    with Session(engine) as session:
        game = session.get_one(Game, game_id)
        record_performance(session, game)

    expected = game_accuracy([0.0, 20.0])
    assert game.accuracy == pytest.approx(expected)
    assert game.performance_rating == pytest.approx(performance_rating(1200, expected))


async def test_a_game_with_no_classified_moves_has_no_performance(engine: Engine) -> None:
    game_id = await two_move_game(engine)

    with Session(engine) as session:
        game = session.get_one(Game, game_id)

    assert (game.accuracy, game.performance_rating) == (None, None)


async def test_finishing_a_game_stores_performance_when_moves_are_classified(
    engine: Engine,
) -> None:
    service = GameService(engine, ScriptedOpponent("e5", "Nc6"))
    game = await service.create_game(PLAYER_ID, 1200, ColorChoice.WHITE)
    await service.play_move(game.id, "g1f3")
    await service.play_move(game.id, "b1c3")
    set_evals(engine, game.id, {1: (50.0, 50.0), 3: (50.0, 50.0)})
    service.resign(game.id)

    finished = service.get_game(game.id)
    assert finished.accuracy == pytest.approx(PERFECT)
    assert finished.performance_rating == performance_rating(1200, PERFECT)


def set_rating(engine: Engine, rating: float) -> None:
    with Session(engine) as session:
        session.get_one(Player, PLAYER_ID).rating = rating
        session.commit()


@pytest.mark.parametrize(
    ("mode", "rating", "expected"),
    [
        (GameMode.REGULAR, 1230.0, 1250),
        (GameMode.PASSIVE, 1230.0, 1350),
        (GameMode.ACTIVE, 1230.0, 1350),
        (GameMode.REGULAR, 100.0, 400),
        (GameMode.ACTIVE, 2990.0, 3000),
    ],
)
async def test_omitted_opponent_elo_defaults_by_mode(
    engine: Engine, mode: GameMode, rating: float, expected: int
) -> None:
    set_rating(engine, rating)
    service = GameService(engine, ScriptedOpponent())

    game = await service.create_game(PLAYER_ID, None, ColorChoice.WHITE, mode)

    assert game.opponent_elo == expected


def test_api_defaults_opponent_elo_and_returns_performance(settings: Settings) -> None:
    app = create_app(settings, ScriptedOpponent("e5", "Nc6"))
    with TestClient(app) as api:
        game = api.post("/games", json={"mode": "passive", "color": "white"}).json()
        assert game["opponent_elo"] == 900  # default rating 800 + 100 stretch
        assert (game["accuracy"], game["performance_rating"]) == (None, None)
        api.post(f"/games/{game['id']}/moves", json={"uci": "g1f3"})
        api.post(f"/games/{game['id']}/moves", json={"uci": "b1c3"})
        set_evals(app.state.engine, game["id"], {1: (50.0, 50.0), 3: (50.0, 50.0)})
        api.post(f"/games/{game['id']}/resign")
        finished = api.get(f"/games/{game['id']}").json()
        history = api.get("/player/rating-history").json()

    expected = performance_rating(900, PERFECT)
    assert (finished["accuracy"], finished["performance_rating"]) == (
        pytest.approx(PERFECT),
        expected,
    )
    assert (history[0]["accuracy"], history[0]["performance_rating"]) == (
        pytest.approx(PERFECT),
        expected,
    )


async def test_accuracy_for_a_black_player_uses_the_even_plies(engine: Engine) -> None:
    service = GameService(engine, ScriptedOpponent("e4", "Nf3", "Nc3"))
    game = await service.create_game(PLAYER_ID, 1200, ColorChoice.BLACK)
    await service.play_move(game.id, "e7e5")
    await service.play_move(game.id, "b8c6")
    service.resign(game.id)
    # plies 2 and 4 are the black player's; the opponent's plies 1 and 3 must be ignored.
    set_evals(
        engine,
        game.id,
        {1: (50.0, 10.0), 2: (50.0, 50.0), 3: (55.0, 5.0), 4: (60.0, 30.0)},
    )

    with Session(engine) as session:
        stored = session.get_one(Game, game.id)
        record_performance(session, stored)

    assert stored.accuracy == pytest.approx(game_accuracy([0.0, 30.0]))
