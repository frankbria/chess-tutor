import asyncio
import json
import socket
from collections.abc import AsyncIterator, Iterator

import httpx2
import pytest
import uvicorn
from fastapi.testclient import TestClient
from scripted_opponent import ScriptedOpponent

from chess_tutor.api.games import status_for
from chess_tutor.games.service import NotPlayersTurnError
from chess_tutor.main import create_app
from chess_tutor.settings import Settings


@pytest.fixture
def api(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings, ScriptedOpponent("e5", "Nc6", "a6", "Qf6"))
    with TestClient(app) as test_client:
        yield test_client


def create(api: TestClient, color: str = "white", elo: int = 1200) -> dict[str, object]:
    response = api.post("/games", json={"mode": "passive", "color": color, "opponent_elo": elo})
    assert response.status_code == 201
    body: dict[str, object] = response.json()
    return body


def test_create_game_describes_the_starting_position(api: TestClient) -> None:
    game = create(api)

    assert game["mode"] == "passive"
    assert (game["player_color"], game["status"], game["result"]) == ("white", "active", None)
    assert game["fen"] == "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    assert (game["moves"], game["side_to_move"]) == ([], "white")
    assert isinstance(game["legal_moves"], list)
    assert len(game["legal_moves"]) == 20 and "e2e4" in game["legal_moves"]


def test_black_player_sees_the_opponents_opening_move(settings: Settings) -> None:
    with TestClient(create_app(settings, ScriptedOpponent("e4"))) as api:
        game = create(api, color="black")

    assert game["moves"] == ["e4"]
    assert game["side_to_move"] == "black"


def test_move_returns_game_with_the_reply(api: TestClient) -> None:
    game = create(api)

    response = api.post(f"/games/{game['id']}/moves", json={"uci": "e2e4"})

    assert response.status_code == 200
    assert response.json()["moves"] == ["e4", "e5"]
    assert api.get(f"/games/{game['id']}").json()["moves"] == ["e4", "e5"]


def test_list_filters_by_status(api: TestClient) -> None:
    active, resigned = create(api), create(api)
    api.post(f"/games/{resigned['id']}/resign")

    def ids(query: str) -> list[int]:
        return [game["id"] for game in api.get(f"/games{query}").json()]

    assert ids("?status=active") == [active["id"]]
    assert ids("?status=finished") == [resigned["id"]]
    assert ids("") == [resigned["id"], active["id"]]
    assert api.get("/games?status=bogus").status_code == 422


def test_resign_finishes_the_game_and_serves_pgn(api: TestClient) -> None:
    game = create(api)

    response = api.post(f"/games/{game['id']}/resign")
    pgn = api.get(f"/games/{game['id']}/pgn")

    assert (response.json()["status"], response.json()["result"]) == ("finished", "0-1")
    assert response.json()["legal_moves"] == []
    assert pgn.headers["content-type"].startswith("text/plain")
    assert '[Result "0-1"]' in pgn.text


@pytest.mark.parametrize(
    ("method", "path", "body", "status"),
    [
        ("GET", "/games/99", None, 404),
        ("GET", "/games/99/pgn", None, 404),
        ("POST", "/games/99/resign", None, 404),
        ("POST", "/games/99/moves", {"uci": "e2e4"}, 404),
        ("GET", "/games/99/coach/stream", None, 404),
        ("POST", "/games", {"opponent_elo": 5}, 422),
        ("POST", "/games", {"opponent_elo": 1200, "color": "green"}, 422),
    ],
)
def test_errors_have_a_status_and_detail(
    api: TestClient, method: str, path: str, body: dict[str, object] | None, status: int
) -> None:
    response = api.request(method, path, json=body)

    assert response.status_code == status
    assert response.json()["detail"]


def test_illegal_move_is_422(api: TestClient) -> None:
    game = create(api)

    response = api.post(f"/games/{game['id']}/moves", json={"uci": "e2e5"})

    assert response.status_code == 422
    assert "not legal" in response.json()["detail"]


def test_player_checkmate_publishes_game_over(settings: Settings) -> None:
    app = create_app(settings, ScriptedOpponent("f3", "g4"))
    with TestClient(app) as api:
        game = create(api, color="black")
        with app.state.game_events.subscribe(game["id"]) as queue:
            api.post(f"/games/{game['id']}/moves", json={"uci": "e7e5"})
            final = api.post(f"/games/{game['id']}/moves", json={"uci": "d8h4"}).json()
            events = [queue.get_nowait() for _ in range(2)]

    assert (final["status"], final["result"]) == ("finished", "0-1")
    assert [event.type for event in events] == ["opponent_move", "game_over"]
    assert events[-1].reason == "checkmate"


def test_move_in_a_finished_game_is_409(api: TestClient) -> None:
    game = create(api)
    api.post(f"/games/{game['id']}/resign")

    assert api.post(f"/games/{game['id']}/moves", json={"uci": "e2e4"}).status_code == 409
    assert api.post(f"/games/{game['id']}/resign").status_code == 409


def test_moving_out_of_turn_is_409(settings: Settings) -> None:
    with TestClient(create_app(settings, ScriptedOpponent())) as api:
        game = create(api)
        assert api.post(f"/games/{game['id']}/moves", json={"uci": "e2e4"}).status_code == 503

        response = api.post(f"/games/{game['id']}/moves", json={"uci": "d2d4"})

    assert response.status_code == 409
    assert "opponent's turn" in response.json()["detail"]


def test_opponent_failure_is_503_and_keeps_the_move(settings: Settings) -> None:
    with TestClient(create_app(settings, ScriptedOpponent())) as api:
        game = create(api)

        response = api.post(f"/games/{game['id']}/moves", json={"uci": "e2e4"})

        assert response.status_code == 503
        assert api.get(f"/games/{game['id']}").json()["moves"] == ["e4"]


def test_games_are_unavailable_without_an_opponent(client: TestClient) -> None:
    response = client.get("/games")

    assert response.status_code == 503


def test_openapi_lists_the_game_routes(api: TestClient) -> None:
    paths = api.get("/openapi.json").json()["paths"]

    assert {
        "/games",
        "/games/{game_id}",
        "/games/{game_id}/moves",
        "/games/{game_id}/resign",
    } <= set(paths)
    assert {"/games/{game_id}/pgn", "/games/{game_id}/coach/stream"} <= set(paths)
    assert api.get("/docs").status_code == 200


async def read_events(response: httpx2.Response, count: int) -> list[tuple[str, dict[str, str]]]:
    events: list[tuple[str, dict[str, str]]] = []
    name = ""
    async for line in response.aiter_lines():
        if line.startswith("event:"):
            name = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            events.append((name, json.loads(line.removeprefix("data:"))))
            if len(events) == count:
                break
    return events


@pytest.fixture
async def live_client(settings: Settings) -> AsyncIterator[httpx2.AsyncClient]:
    # ASGITransport buffers whole responses, so a stream needs a real server on a free port.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config = uvicorn.Config(
        create_app(settings, ScriptedOpponent("e5", "Nc6")),
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    serving = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    async with httpx2.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
        yield client
    server.should_exit = True
    await serving


async def test_stream_delivers_opponent_move_then_game_over(
    live_client: httpx2.AsyncClient,
) -> None:
    client = live_client
    game_id = (await client.post("/games", json={"color": "white", "opponent_elo": 1000})).json()[
        "id"
    ]

    async with client.stream("GET", f"/games/{game_id}/coach/stream") as stream:
        assert stream.headers["content-type"].startswith("text/event-stream")
        reader = asyncio.create_task(read_events(stream, 3))
        await client.post(f"/games/{game_id}/moves", json={"uci": "e2e4"})
        await client.post(f"/games/{game_id}/resign")
        events = await asyncio.wait_for(reader, timeout=5)

    assert events[0][0] == "snapshot"
    assert events[1:] == [
        (
            "opponent_move",
            {
                "type": "opponent_move",
                "uci": "e7e5",
                "san": "e5",
                "fen": "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
            },
        ),
        ("game_over", {"type": "game_over", "result": "0-1", "reason": "resignation"}),
    ]


async def test_stream_ends_after_game_over(live_client: httpx2.AsyncClient) -> None:
    game_id = (
        await live_client.post("/games", json={"opponent_elo": 1000, "color": "white"})
    ).json()["id"]

    async with live_client.stream("GET", f"/games/{game_id}/coach/stream") as stream:
        reader = asyncio.create_task(read_events(stream, 99))
        await live_client.post(f"/games/{game_id}/resign")
        events = await asyncio.wait_for(reader, timeout=5)  # returns only because the stream closed

    assert [name for name, _ in events] == ["snapshot", "game_over"]


async def test_stream_of_a_finished_game_is_a_snapshot_then_closes(
    live_client: httpx2.AsyncClient,
) -> None:
    game_id = (
        await live_client.post("/games", json={"opponent_elo": 1000, "color": "white"})
    ).json()["id"]
    await live_client.post(f"/games/{game_id}/resign")

    async with live_client.stream("GET", f"/games/{game_id}/coach/stream") as stream:
        events = await asyncio.wait_for(read_events(stream, 99), timeout=5)

    assert [(name, data["status"]) for name, data in events] == [("snapshot", "finished")]


def test_snapshot_shows_the_opponents_first_move_for_a_black_player(settings: Settings) -> None:
    app = create_app(settings, ScriptedOpponent("e4"))
    with TestClient(app) as api:
        game = create(api, color="black")
        with api.stream("GET", f"/games/{game['id']}/coach/stream") as stream:
            data_line = next(line for line in stream.iter_lines() if line.startswith("data:"))

    snapshot = json.loads(data_line.removeprefix("data:"))
    assert snapshot["type"] == "snapshot"
    assert snapshot["fen"].startswith("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP")
    assert (snapshot["side_to_move"], snapshot["last_move"]) == ("black", "e4")


def test_subclass_of_a_mapped_error_maps_to_its_base_status() -> None:
    class StaleMoveError(NotPlayersTurnError):
        pass

    assert status_for(StaleMoveError("late")) == 409
