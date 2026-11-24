from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from scripted_opponent import ScriptedOpponent
from sqlalchemy.orm import Session

from chess_tutor.main import create_app
from chess_tutor.models import (
    ConceptState,
    ConceptStatus,
    Game,
    GameMode,
    GameResult,
    GameStatus,
    GameSummary,
    PlayerColor,
)
from chess_tutor.settings import Settings


def finished_game(ended_at: datetime, accuracy: float | None = None) -> Game:
    return Game(
        player_id=1,
        mode=GameMode.ACTIVE,
        player_color=PlayerColor.BLACK,
        opponent_engine="maia3",
        opponent_elo=1300,
        status=GameStatus.FINISHED,
        result=GameResult.BLACK_WINS,
        accuracy=accuracy,
        ended_at=ended_at,
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings, ScriptedOpponent("e4"))) as test_client:
        yield test_client


def test_no_choice_is_remembered_before_the_first_game(client: TestClient) -> None:
    assert client.get("/player/last-choice").json() == {
        "mode": None,
        "color": None,
        "opponent_elo": None,
    }


def test_creating_a_game_remembers_its_mode_colour_choice_and_elo(client: TestClient) -> None:
    assert (
        client.post(
            "/games", json={"mode": "active", "color": "random", "opponent_elo": 1450}
        ).status_code
        == 201
    )

    assert client.get("/player/last-choice").json() == {
        "mode": "active",
        "color": "random",
        "opponent_elo": 1450,
    }


def test_the_remembered_elo_is_the_one_the_server_chose_when_none_was_sent(
    client: TestClient,
) -> None:
    created = client.post("/games", json={"mode": "regular", "color": "white"}).json()

    assert client.get("/player/last-choice").json()["opponent_elo"] == created["opponent_elo"]


def test_a_rejected_game_does_not_overwrite_the_remembered_choice(client: TestClient) -> None:
    client.post("/games", json={"mode": "passive", "color": "white", "opponent_elo": 1000})
    rejected = client.post("/games", json={"mode": "active", "color": "black", "opponent_elo": 5})

    assert rejected.status_code == 422
    assert client.get("/player/last-choice").json()["mode"] == "passive"


def test_there_is_no_last_game_before_one_is_finished(client: TestClient) -> None:
    client.post("/games", json={"mode": "regular", "color": "white"})

    assert client.get("/player/last-game").json() is None


def test_the_last_game_is_the_newest_finished_one_with_its_summary(settings: Settings) -> None:
    app = create_app(settings)
    now = datetime.now(UTC)
    with TestClient(app) as client, Session(app.state.engine) as session:
        older = finished_game(now - timedelta(days=1), accuracy=60.0)
        newer = finished_game(now, accuracy=82.5)
        session.add_all([older, newer])
        session.flush()
        newer_id = newer.id
        session.add(
            GameSummary(
                game_id=newer_id,
                player_id=1,
                summary="You held the centre.",
                key_moments="[]",
                concepts="[]",
                source="template",
            )
        )
        session.commit()

        body = client.get("/player/last-game").json()

    assert body == {
        "game_id": newer_id,
        "mode": "active",
        "player_color": "black",
        "opponent_elo": 1300,
        "result": "0-1",
        "accuracy": 82.5,
        "summary": "You held the centre.",
    }


def test_a_finished_game_without_a_summary_still_has_a_recap(settings: Settings) -> None:
    app = create_app(settings)
    with TestClient(app) as client, Session(app.state.engine) as session:
        session.add(finished_game(datetime.now(UTC)))
        session.commit()

        body = client.get("/player/last-game").json()

    assert body["summary"] is None and body["accuracy"] is None


def test_concepts_report_when_their_state_last_changed(settings: Settings) -> None:
    app = create_app(settings)
    changed = datetime(2026, 11, 20, tzinfo=UTC)
    with TestClient(app) as client, Session(app.state.engine) as session:
        session.add(finished_game(changed))
        session.flush()
        session.add(
            ConceptState(
                player_id=1,
                concept="back_rank",
                state=ConceptStatus.IMPROVING,
                count=3,
                recency_score=0.5,
                games_since_last_seen=3,
                last_processed_game_id=1,
                updated_at=changed,
            )
        )
        session.commit()

        body = client.get("/player/concepts").json()

    assert body[0]["updated_at"].startswith("2026-11-20")
