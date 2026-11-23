from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from chess_tutor.main import create_app
from chess_tutor.models import Game, GameMode, GameStatus, PlayerColor
from chess_tutor.settings import Settings


def seed_games(settings: Settings, costs_and_dates: list[tuple[float, datetime]]) -> None:
    app = create_app(settings)
    with TestClient(app), Session(app.state.engine) as session:
        for cost, started_at in costs_and_dates:
            session.add(
                Game(
                    player_id=1,
                    mode=GameMode.PASSIVE,
                    player_color=PlayerColor.WHITE,
                    opponent_engine="maia3",
                    opponent_elo=1200,
                    status=GameStatus.FINISHED,
                    llm_cost=cost,
                    started_at=started_at,
                )
            )
        session.commit()


def test_defaults_come_from_configuration(client: TestClient, settings: Settings) -> None:
    body = client.get("/settings").json()

    assert body["preferences"] == {
        "coach_verbosity": "normal",
        "default_mode": "passive",
        "maia_model": settings.maia_model,
        "monthly_cost_ceiling": 10.0,
    }
    assert body["monthly_cost"] == 0.0
    assert body["budget_alert"] is False
    assert body["recent_games"] == []


def test_put_persists_across_restarts(settings: Settings) -> None:
    preferences = {
        "coach_verbosity": "brief",
        "default_mode": "active",
        "maia_model": "23m",
        "monthly_cost_ceiling": 4.5,
    }
    with TestClient(create_app(settings)) as first:
        response = first.put("/settings", json=preferences)
        assert response.status_code == 200
        assert response.json()["preferences"] == preferences

    with TestClient(create_app(settings)) as second:
        assert second.get("/settings").json()["preferences"] == preferences


def test_put_replaces_existing_values(client: TestClient) -> None:
    base = {
        "coach_verbosity": "brief",
        "default_mode": "regular",
        "maia_model": "5m",
        "monthly_cost_ceiling": 5.0,
    }
    client.put("/settings", json=base)
    client.put("/settings", json={**base, "coach_verbosity": "detailed"})

    assert client.get("/settings").json()["preferences"]["coach_verbosity"] == "detailed"


@pytest.mark.parametrize(
    "change",
    [
        {"coach_verbosity": "loud"},
        {"default_mode": "blitz"},
        {"maia_model": "1b"},
        {"monthly_cost_ceiling": 0},
        {"monthly_cost_ceiling": -1},
    ],
)
def test_put_rejects_invalid_values(client: TestClient, change: dict[str, object]) -> None:
    valid = {
        "coach_verbosity": "normal",
        "default_mode": "passive",
        "maia_model": "5m",
        "monthly_cost_ceiling": 10.0,
    }

    assert client.put("/settings", json={**valid, **change}).status_code == 422


def test_cost_counts_this_month_only_and_alerts_at_eighty_percent(settings: Settings) -> None:
    now = datetime.now(UTC)
    last_month = now.replace(year=now.year - 1)
    seed_games(settings, [(4.0, now), (4.0, now), (50.0, last_month)])

    with TestClient(create_app(settings)) as client:
        body = client.get("/settings").json()
        assert body["monthly_cost"] == 8.0
        assert body["budget_alert"] is True  # 8.0 of the default 10.0 is exactly 80%
        assert sorted(game["llm_cost"] for game in body["recent_games"]) == [4.0, 4.0, 50.0]

        client.put(
            "/settings",
            json={**body["preferences"], "monthly_cost_ceiling": 10.01},
        )
        assert client.get("/settings").json()["budget_alert"] is False


def test_put_is_allowed_by_cors(settings: Settings) -> None:
    client = TestClient(create_app(settings))
    response = client.options(
        "/settings",
        headers={
            "Origin": settings.frontend_origins[0],
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert "PUT" in response.headers["access-control-allow-methods"]


def test_stored_maia_model_is_what_the_app_starts_with(settings: Settings) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        assert app.state.settings.maia_model == settings.maia_model
        preferences = client.get("/settings").json()["preferences"]
        client.put("/settings", json={**preferences, "maia_model": "23m"})

    restarted = create_app(settings)
    with TestClient(restarted):
        assert restarted.state.settings.maia_model == "23m"
