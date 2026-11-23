from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from scripted_opponent import ScriptedOpponent
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from chess_tutor.database import create_database_engine, run_migrations, seed_default_player
from chess_tutor.games.service import ColorChoice, GameService
from chess_tutor.main import create_app
from chess_tutor.models import Game, GameMode, GameResult, Player, RatingHistory
from chess_tutor.rating.glicko2 import GameOutcome, Rating, update, weighted_update
from chess_tutor.rating.history import record_rating
from chess_tutor.settings import Settings, Thresholds

PLAYER_ID = 1


def test_matches_glickmans_worked_example() -> None:
    result = update(
        Rating(1500, 200, 0.06),
        [GameOutcome(1400, 30, 1), GameOutcome(1550, 100, 0), GameOutcome(1700, 300, 0)],
    )

    assert result.rating == pytest.approx(1464.06, abs=0.01)
    assert result.deviation == pytest.approx(151.52, abs=0.01)
    assert result.volatility == pytest.approx(0.05999, abs=0.00001)


def test_no_games_only_grows_the_deviation() -> None:
    result = update(Rating(1500, 200, 0.06), [])

    assert result.rating == 1500
    assert result.deviation > 200


def test_win_raises_loss_lowers_and_draw_against_a_stronger_opponent_raises() -> None:
    player = Rating(1000, 200, 0.06)

    assert update(player, [GameOutcome(1000, 50, 1)]).rating > 1000
    assert update(player, [GameOutcome(1000, 50, 0)]).rating < 1000
    assert update(player, [GameOutcome(1400, 50, 0.5)]).rating > 1000


def test_deviation_shrinks_after_a_game() -> None:
    assert update(Rating(800, 350, 0.06), [GameOutcome(800, 50, 1)]).deviation < 350


def test_weight_scales_the_change() -> None:
    player = Rating(800, 350, 0.06)
    games = [GameOutcome(1000, 50, 1)]
    full = weighted_update(player, games, 1.0)
    half = weighted_update(player, games, 0.5)

    assert half.rating - 800 == pytest.approx((full.rating - 800) / 2)
    assert 800 < half.rating < full.rating
    assert half.deviation > full.deviation


@pytest.fixture
def engine(settings: Settings) -> Iterator[Engine]:
    run_migrations(settings)
    database_engine = create_database_engine(settings)
    seed_default_player(database_engine)
    yield database_engine
    database_engine.dispose()


def player_rating(engine: Engine) -> float:
    with Session(engine) as session:
        return session.get_one(Player, PLAYER_ID).rating


def history_count(engine: Engine) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(RatingHistory)) or 0


async def resigned_game(engine: Engine, mode: GameMode) -> int:
    service = GameService(engine, ScriptedOpponent())
    game = await service.create_game(PLAYER_ID, 1200, ColorChoice.WHITE, mode)
    service.resign(game.id)
    return game.id


async def test_resigning_lowers_rating_and_writes_one_history_row(engine: Engine) -> None:
    game_id = await resigned_game(engine, GameMode.REGULAR)

    with Session(engine) as session:
        row = session.scalars(select(RatingHistory)).one()
        assert (row.game_id, row.weight, row.rating_before) == (game_id, 1.0, 800.0)
        assert row.rating_after < 800 == row.rating_before
        assert row.deviation_after < row.deviation_before
        assert session.get_one(Player, PLAYER_ID).rating == row.rating_after


async def test_tutor_game_moves_the_rating_less_than_a_regular_one(engine: Engine) -> None:
    await resigned_game(engine, GameMode.REGULAR)
    regular_loss = 800 - player_rating(engine)
    with Session(engine) as session:
        player = session.get_one(Player, PLAYER_ID)
        player.rating = 800.0
        session.commit()

    await resigned_game(engine, GameMode.ACTIVE)

    assert 0 < 800 - player_rating(engine) < regular_loss


async def test_checkmate_win_raises_rating_and_finishing_twice_does_not_reapply(
    engine: Engine,
) -> None:
    service = GameService(engine, ScriptedOpponent("f3", "g4"))
    game = await service.create_game(PLAYER_ID, 1200, ColorChoice.BLACK)
    await service.play_move(game.id, "e7e5")
    finished = await service.play_move(game.id, "d8h4")
    rating_after_win = player_rating(engine)

    assert finished.status.value == "finished"
    assert rating_after_win > 800
    assert history_count(engine) == 1
    with pytest.raises(Exception, match="already finished"):
        service.resign(game.id)
    assert (player_rating(engine), history_count(engine)) == (rating_after_win, 1)


async def test_draw_against_a_stronger_opponent_raises_the_rating(engine: Engine) -> None:
    service = GameService(engine, ScriptedOpponent())
    game = await service.create_game(PLAYER_ID, 1600, ColorChoice.WHITE)
    with Session(engine) as session:
        stored = session.get_one(Game, game.id)
        stored.result = GameResult.DRAW
        record_rating(session, stored, Thresholds())
        record_rating(session, stored, Thresholds())
        session.commit()

    assert player_rating(engine) > 800
    assert history_count(engine) == 1


def test_rating_history_endpoint_lists_rows(settings: Settings) -> None:
    with TestClient(create_app(settings, ScriptedOpponent())) as api:
        assert api.get("/player/rating-history").json() == []
        game = api.post(
            "/games", json={"mode": "regular", "color": "white", "opponent_elo": 1200}
        ).json()
        api.post(f"/games/{game['id']}/resign")
        rows = api.get("/player/rating-history").json()

    assert len(rows) == 1
    assert rows[0]["game_id"] == game["id"]
    assert rows[0]["rating_after"] < rows[0]["rating_before"] == 800.0
    assert rows[0]["weight"] == 1.0
