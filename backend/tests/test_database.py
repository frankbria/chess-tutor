from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from chess_tutor.database import run_migrations
from chess_tutor.main import create_app
from chess_tutor.models import Player
from chess_tutor.settings import Settings


def test_migration_creates_schema_on_fresh_database(settings: Settings) -> None:
    run_migrations(settings)

    inspector = inspect(create_engine(f"sqlite:///{settings.database_path}"))
    assert {"player", "settings"} <= set(inspector.get_table_names())
    assert [c["name"] for c in inspector.get_columns("player")] == [
        "id",
        "display_name",
        "rating",
        "rating_deviation",
        "volatility",
        "created_at",
    ]
    assert inspector.get_pk_constraint("settings")["constrained_columns"] == ["player_id", "key"]


def test_default_player_seeded_once_across_startups(settings: Settings) -> None:
    for _ in range(2):
        with TestClient(create_app(settings)):
            pass

    with Session(create_engine(f"sqlite:///{settings.database_path}")) as session:
        players = session.scalars(select(Player)).all()
    assert len(players) == 1
    assert (players[0].rating, players[0].rating_deviation, players[0].volatility) == (
        800,
        350,
        0.06,
    )


def test_get_player_returns_default_player(client: TestClient) -> None:
    response = client.get("/player")

    assert response.status_code == 200
    assert response.json() == {
        "id": 1,
        "display_name": "Player",
        "rating": 800,
        "rating_deviation": 350,
    }
