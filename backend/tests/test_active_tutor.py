from collections.abc import Awaitable, Iterator

import chess
import chess.engine
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from scripted_opponent import ScriptedOpponent
from sqlalchemy import select
from sqlalchemy.orm import Session

from chess_tutor.coach.active import ActiveTutor
from chess_tutor.engines.stockfish import AnalysedLine, Budget, StockfishPool
from chess_tutor.main import create_app
from chess_tutor.models import Move, MoveProposal
from chess_tutor.settings import Settings


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings, ScriptedOpponent("e5", "Nc6", "a6"))


@pytest.fixture
def api(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def create(api: TestClient, mode: str = "active") -> int:
    body = {"mode": mode, "color": "white", "opponent_elo": 1200}
    response = api.post("/games", json=body)
    assert response.status_code == 201
    game_id: int = response.json()["id"]
    return game_id


def propose(api: TestClient, game_id: int, uci: str) -> dict[str, object]:
    response = api.post(f"/games/{game_id}/moves", json={"uci": uci, "propose": True})
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


def test_a_proposal_leaves_the_game_unchanged_and_returns_the_analysis(api: TestClient) -> None:
    game_id = create(api)
    before = api.get(f"/games/{game_id}").json()

    proposal = propose(api, game_id, "e2e4")

    assert api.get(f"/games/{game_id}").json() == before
    assert (proposal["uci"], proposal["san"], proposal["rethinks"]) == ("e2e4", "e4", 0)
    assert isinstance(proposal["analysis_id"], int)
    assert 0 <= float(str(proposal["win_probability_after"])) <= 100


def test_the_proposal_response_never_names_the_best_move(api: TestClient) -> None:
    game_id = create(api)

    proposal = propose(api, game_id, "a2a3")

    assert not {"best_move", "best_line", "best_move_san"} & proposal.keys()


def test_lock_in_commits_exactly_the_proposed_move(app: FastAPI, api: TestClient) -> None:
    game_id = create(api)
    propose(api, game_id, "d2d4")

    response = api.post(f"/games/{game_id}/moves/lock")

    assert response.status_code == 200
    assert response.json()["moves"] == ["d4", "e5"]
    with Session(app.state.engine) as session:
        stored = session.scalars(select(Move).where(Move.ply == 1)).one()
        assert (stored.uci, stored.proposed_attempts) == ("d2d4", 0)
        assert session.scalars(select(MoveProposal)).all() == []


def test_rethinks_are_counted_and_only_the_last_proposal_is_committed(
    app: FastAPI, api: TestClient
) -> None:
    game_id = create(api)
    propose(api, game_id, "a2a4")
    assert api.post(f"/games/{game_id}/moves/rethink").json() == {"rethinks": 1}
    propose(api, game_id, "h2h4")
    assert api.post(f"/games/{game_id}/moves/rethink").json() == {"rethinks": 2}
    again = propose(api, game_id, "e2e4")
    assert again["rethinks"] == 2

    api.post(f"/games/{game_id}/moves/lock")

    with Session(app.state.engine) as session:
        stored = session.scalars(select(Move).where(Move.ply == 1)).one()
        assert (stored.uci, stored.proposed_attempts) == ("e2e4", 2)


def test_the_attempt_count_starts_again_on_the_next_move(app: FastAPI, api: TestClient) -> None:
    game_id = create(api)
    propose(api, game_id, "a2a4")
    api.post(f"/games/{game_id}/moves/rethink")
    propose(api, game_id, "e2e4")
    api.post(f"/games/{game_id}/moves/lock")

    assert propose(api, game_id, "g1f3")["rethinks"] == 0


def test_a_rethink_discards_the_proposal(api: TestClient) -> None:
    game_id = create(api)
    propose(api, game_id, "e2e4")
    api.post(f"/games/{game_id}/moves/rethink")

    assert api.post(f"/games/{game_id}/moves/lock").status_code == 409
    assert api.get(f"/games/{game_id}").json()["moves"] == []


def test_lock_and_rethink_need_a_pending_proposal(api: TestClient) -> None:
    game_id = create(api)

    assert api.post(f"/games/{game_id}/moves/lock").status_code == 409
    assert api.post(f"/games/{game_id}/moves/rethink").status_code == 409


def test_a_second_proposal_waits_for_a_rethink(api: TestClient) -> None:
    game_id = create(api)
    propose(api, game_id, "e2e4")

    response = api.post(f"/games/{game_id}/moves", json={"uci": "d2d4", "propose": True})

    assert response.status_code == 409


def test_an_illegal_proposal_is_rejected_and_stores_nothing(app: FastAPI, api: TestClient) -> None:
    game_id = create(api)

    response = api.post(f"/games/{game_id}/moves", json={"uci": "e2e5", "propose": True})

    assert response.status_code == 422
    with Session(app.state.engine) as session:
        assert session.scalars(select(MoveProposal)).all() == []


@pytest.mark.parametrize("mode", ["regular", "passive"])
def test_only_active_games_take_proposals(api: TestClient, mode: str) -> None:
    game_id = create(api, mode)

    response = api.post(f"/games/{game_id}/moves", json={"uci": "e2e4", "propose": True})

    assert response.status_code == 409


def test_a_proposal_for_an_unknown_game_is_a_404(api: TestClient) -> None:
    assert api.post("/games/99/moves", json={"uci": "e2e4", "propose": True}).status_code == 404
    assert api.post("/games/99/moves/lock").status_code == 404
    assert api.post("/games/99/moves/rethink").status_code == 404


def test_no_proposal_after_the_game_ended(api: TestClient) -> None:
    game_id = create(api)
    propose(api, game_id, "e2e4")
    api.post(f"/games/{game_id}/moves/lock")  # the opponent has already replied: our turn again
    api.post(f"/games/{game_id}/resign")

    response = api.post(f"/games/{game_id}/moves", json={"uci": "g1f3", "propose": True})

    assert response.status_code == 409


def test_the_pending_proposal_survives_a_restart(settings: Settings) -> None:
    with TestClient(create_app(settings, ScriptedOpponent("e5"))) as first:
        game_id = create(first)
        propose(first, game_id, "e2e4")
    with TestClient(create_app(settings, ScriptedOpponent("e5"))) as second:
        assert second.post(f"/games/{game_id}/moves/lock").json()["moves"] == ["e4", "e5"]


def test_lock_in_is_blocked_after_the_game_ended(api: TestClient) -> None:
    game_id = create(api)
    propose(api, game_id, "e2e4")
    api.post(f"/games/{game_id}/resign")

    assert api.post(f"/games/{game_id}/moves/lock").status_code == 409


def test_a_plain_move_in_an_active_game_is_refused(app: FastAPI, api: TestClient) -> None:
    game_id = create(api)

    response = api.post(f"/games/{game_id}/moves", json={"uci": "e2e4"})

    assert response.status_code == 409
    assert api.get(f"/games/{game_id}").json()["moves"] == []


class BrokenPool(StockfishPool):
    async def analyse(
        self, board: chess.Board, budget: Budget | chess.engine.Limit, multipv: int = 1
    ) -> list[AnalysedLine]:
        raise RuntimeError("engine down")


def test_a_failed_analysis_leaves_no_proposal_behind(app: FastAPI, api: TestClient) -> None:
    game_id = create(api)
    working = app.state.active_tutor
    app.state.active_tutor = ActiveTutor(app.state.game_service, BrokenPool("unused", 1))
    with pytest.raises(RuntimeError):
        api.post(f"/games/{game_id}/moves", json={"uci": "e2e4", "propose": True})
    app.state.active_tutor = working

    assert propose(api, game_id, "e2e4")["uci"] == "e2e4"


def test_the_proposal_carries_its_san_from_when_it_was_stored(
    app: FastAPI, api: TestClient
) -> None:
    game_id = create(api)

    proposal = asyncio_run(api, app.state.game_service.propose_move(game_id, "g1f3"))

    assert proposal.san == "Nf3"


@pytest.mark.parametrize("action", ["lock", "rethink"])
def test_lock_and_rethink_are_for_active_games(api: TestClient, action: str) -> None:
    game_id = create(api, "passive")

    response = api.post(f"/games/{game_id}/moves/{action}")

    assert response.status_code == 409 and "Active" in response.json()["detail"]


def test_a_finished_game_drops_its_pending_proposal(app: FastAPI, api: TestClient) -> None:
    game_id = create(api)
    propose(api, game_id, "e2e4")

    api.post(f"/games/{game_id}/resign")

    with Session(app.state.engine) as session:
        assert session.scalars(select(MoveProposal)).all() == []


def test_the_rethink_response_is_documented(api: TestClient) -> None:
    schema = api.get("/openapi.json").json()
    operation = schema["paths"]["/games/{game_id}/moves/rethink"]["post"]
    ref = operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]

    assert ref.endswith("RethinkResponse")


def asyncio_run[Result](api: TestClient, awaitable: Awaitable[Result]) -> Result:
    assert api.portal is not None

    async def await_it() -> Result:
        return await awaitable

    return api.portal.call(await_it)
