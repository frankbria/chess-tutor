import asyncio
import json
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

import chess
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from scripted_language_model import ScriptedLanguageModel
from scripted_opponent import ScriptedOpponent
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_passive_coach import DownLanguageModel

from chess_tutor.analysis.move_delta import move_delta
from chess_tutor.api.events import CoachCommentEvent
from chess_tutor.coach.active import hint_level
from chess_tutor.coach.llm import (
    ChatMessage,
    LanguageModel,
    LanguageModelError,
    ModelTier,
    StructuredReply,
    Usage,
)
from chess_tutor.coach.prompt import CoachReply
from chess_tutor.engines.stockfish import StockfishPool
from chess_tutor.main import create_app
from chess_tutor.models import CoachMessage, Game
from chess_tutor.settings import Settings

# 1.e4 e5 2.Nf3 Nc6 3.Bc4 Nf6 4.Nxe5?? is a blunder that Nxe5 punishes.
OPPONENT_REPLIES = ("e5", "Nc6", "Nf6")
SETUP_MOVES = ("e2e4", "g1f3", "f1c4")
BLUNDER = "f3e5"
PRICED = Usage(model="claude-sonnet-5-5", input_tokens=1000, output_tokens=100)


class Harness:
    def __init__(self, app: FastAPI, api: TestClient, game_id: int) -> None:
        self.app, self.api, self.game_id = app, api, game_id
        self._subscription = app.state.game_events.subscribe(game_id)
        self.queue = self._subscription.__enter__()

    def post(self, path: str, body: dict[str, object] | None = None) -> dict[str, object]:
        response = self.api.post(f"/games/{self.game_id}/{path}", json=body)
        assert response.status_code == 200, response.text
        result: dict[str, object] = response.json()
        return result

    def settle(self) -> None:
        assert self.api.portal is not None
        self.api.portal.call(self.app.state.hint_coach.wait_until_idle)

    def propose(self, uci: str) -> None:
        self.post("moves", {"uci": uci, "propose": True})
        self.settle()

    def play_setup(self) -> None:
        for uci in SETUP_MOVES:
            self.propose(uci)
            self.post("moves/lock")
            self.settle()

    def stored(self) -> list[CoachMessage]:
        with Session(self.app.state.engine) as session:
            return list(session.scalars(select(CoachMessage).order_by(CoachMessage.id)))

    def events(self) -> list[CoachCommentEvent]:
        found = [self.queue.get_nowait() for _ in range(self.queue.qsize())]
        return [event for event in found if isinstance(event, CoachCommentEvent)]


@contextmanager
def started(
    settings: Settings, model: LanguageModel, replies: tuple[str, ...] = OPPONENT_REPLIES
) -> Iterator[Harness]:
    app = create_app(settings, ScriptedOpponent(*replies), model)
    with TestClient(app) as api:
        body = {"mode": "active", "color": "white", "opponent_elo": 1200}
        yield Harness(app, api, api.post("/games", json=body).json()["id"])


def test_the_hint_level_follows_the_rethink_count() -> None:
    assert [hint_level(rethinks) for rethinks in range(5)] == [1, 2, 3, 3, 3]


def best_move_before_blunder(settings: Settings) -> str:
    board = chess.Board()
    for san in ("e4", "e5", "Nf3", "Nc6", "Bc4", "Nf6"):
        board.push_san(san)

    async def best() -> str:
        async with StockfishPool(settings.stockfish_path, 1) as pool:
            delta = await move_delta(board, chess.Move.from_uci(BLUNDER), pool)
        assert delta.best_move_san is not None
        return delta.best_move_san

    return asyncio.run(best())


def test_three_rethinks_climb_nudge_pointer_consequence(settings: Settings) -> None:
    model = ScriptedLanguageModel(PRICED)
    model.queue_structured(CoachReply(text="What can your opponent do after this?", fact_ids=[]))
    model.queue_structured(
        CoachReply(text="Take a look at the knight on c6.", fact_ids=["allows:c6e5"])
    )
    model.queue_structured(
        CoachReply(
            text="After Nxe5, Black has Nxe5 and you lose a piece.", fact_ids=["allows:c6e5"]
        )
    )
    best = best_move_before_blunder(settings)
    with started(settings, model) as harness:
        harness.play_setup()
        for _ in range(3):
            harness.propose(BLUNDER)
            harness.post("moves/rethink")

        stored = harness.stored()
        assert [(m.kind.value, m.hint_level, m.ply) for m in stored] == [("hint", 1, 7)] + [
            ("hint", 2, 7),
            ("hint", 3, 7),
        ]
        assert [event.hint_level for event in harness.events()] == [1, 2, 3]
    payloads = [json.loads(call.messages[0].content) for call in model.calls]
    assert [payload["hint_level"] for payload in payloads] == [1, 2, 3]
    for payload in payloads:
        assert payload["event"] == "hint" and payload["locked_in"] is False
        assert not {"best_move", "best_line"} & payload.keys()
        assert best not in payload["allowed_references"]["moves"]
        assert best not in [
            san for fact in payload["facts"] for line in fact.get("san_lines", []) for san in line
        ]
        assert best not in " ".join(fact["summary"] for fact in payload["facts"])
    assert all(best not in message.text for message in stored)


def test_the_template_fallback_climbs_the_ladder_without_the_best_move(settings: Settings) -> None:
    best = best_move_before_blunder(settings)
    with started(settings, DownLanguageModel(LanguageModelError("down", PRICED))) as harness:
        harness.play_setup()
        for _ in range(4):
            harness.propose(BLUNDER)
            harness.post("moves/rethink")

        stored = harness.stored()
        assert [message.hint_level for message in stored] == [1, 2, 3, 3]
        assert {message.source.value for message in stored} == {"template"}
        assert "knight on c6" in stored[1].text and "Nxe5" in stored[2].text
        assert all(best not in message.text for message in stored)


def test_a_fine_move_gets_no_comment(settings: Settings) -> None:
    model = ScriptedLanguageModel(PRICED)
    with started(settings, model) as harness:
        harness.propose("e2e4")

        assert model.calls == [] and harness.stored() == [] and harness.events() == []


def test_a_board_feature_remark_is_rate_limited(settings: Settings) -> None:
    every_second = settings.model_copy(update={"active_remark_every_n_proposals": 2})
    model = ScriptedLanguageModel(PRICED)
    model.queue_structured(CoachReply(text="Keep an eye on your development.", fact_ids=[]))
    replies = (*OPPONENT_REPLIES, "Bc5", "d6")
    with started(every_second, model, replies) as harness:
        # Development is first worth a remark before 5.d3; 6.Nc3 is the next proposal, too soon.
        for uci in ("e2e4", "g1f3", "f1c4", "e1g1", "d2d3"):
            harness.propose(uci)
            harness.post("moves/lock")
        harness.propose("b1c3")

        [remark] = harness.stored()
        assert (remark.kind.value, remark.hint_level, remark.ply) == ("comment", None, 9)
        [event] = harness.events()
        assert (event.classification, event.hint_level) == ("remark", None)
    assert len(model.calls) == 1  # 6.Nc3 had a feature too, but came one proposal later
    payload = json.loads(model.calls[0].messages[0].content)
    assert payload["event"] == "comment_on_move" and len(payload["facts"]) == 1
    assert payload["facts"][0]["type"] in {
        "king_safety",
        "pawn_structure",
        "development",
        "activity",
    }
    assert not {"best_move", "best_line"} & payload.keys()


class GatedLanguageModel(ScriptedLanguageModel):
    """Holds each reply until the test lets it through."""

    def __init__(self) -> None:
        super().__init__(PRICED)
        self.asked = asyncio.Event()
        self.release = asyncio.Event()

    async def structured[ParsedModel: BaseModel](
        self,
        *,
        tier: ModelTier,
        system: str,
        messages: Sequence[ChatMessage],
        schema: type[ParsedModel],
    ) -> StructuredReply[ParsedModel]:
        self.asked.set()
        await self.release.wait()
        return await super().structured(tier=tier, system=system, messages=messages, schema=schema)


@pytest.mark.parametrize("interruption", ["moves/rethink", "moves/lock"])
def test_a_hint_that_is_overtaken_never_lands(settings: Settings, interruption: str) -> None:
    model = GatedLanguageModel()
    model.queue_structured(CoachReply(text="What can your opponent do next?", fact_ids=[]))
    with started(settings, model, (*OPPONENT_REPLIES, "Nxe5")) as harness:
        harness.play_setup()
        harness.post("moves", {"uci": BLUNDER, "propose": True})
        assert harness.api.portal is not None
        harness.api.portal.call(model.asked.wait)

        harness.post(interruption)
        harness.api.portal.call(model.release.set)
        harness.settle()

        assert harness.stored() == [] and harness.events() == []
        with Session(harness.app.state.engine) as session:
            assert (
                session.get_one(Game, harness.game_id).llm_cost > 0
            )  # the call was still paid for


def test_without_a_language_model_proposals_get_no_hints(settings: Settings) -> None:
    app = create_app(settings, ScriptedOpponent(*OPPONENT_REPLIES))
    with TestClient(app) as api:
        game_id = api.post("/games", json={"mode": "active", "color": "white"}).json()["id"]
        for uci in SETUP_MOVES:
            api.post(f"/games/{game_id}/moves", json={"uci": uci, "propose": True})
            api.post(f"/games/{game_id}/moves/lock")
        api.post(f"/games/{game_id}/moves", json={"uci": BLUNDER, "propose": True})
        assert api.portal is not None
        api.portal.call(app.state.hint_coach.wait_until_idle)

        with Session(app.state.engine) as session:
            assert session.scalars(select(CoachMessage)).all() == []
