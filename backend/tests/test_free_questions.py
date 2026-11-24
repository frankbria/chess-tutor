import asyncio
import json
from collections.abc import Sequence

import chess
from fastapi.testclient import TestClient
from pydantic import BaseModel
from scripted_language_model import ScriptedLanguageModel
from scripted_opponent import ScriptedOpponent
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_active_hints import BLUNDER, GatedLanguageModel, best_move_before_blunder
from test_active_hints import Harness as ActiveHarness
from test_active_hints import started as started_active
from test_passive_coach import PRICED, Harness, scripted, started

from chess_tutor.analysis.move_delta import san_line
from chess_tutor.api.events import CoachCommentEvent
from chess_tutor.coach.llm import ChatMessage, ModelTier, StructuredReply
from chess_tutor.coach.prompt import CoachReply
from chess_tutor.coach.summary import SummaryDraft
from chess_tutor.engines.stockfish import Budget, StockfishPool
from chess_tutor.facts.packet import build_fact_packet
from chess_tutor.main import create_app
from chess_tutor.models import CoachMessage, Game
from chess_tutor.settings import Settings

QUESTION = "What is he threatening?"
REPLY = CoachReply(text="Take a look at the knight on c6.", fact_ids=["allows:c6e5"])
DRAFT = SummaryDraft(summary="A short game.", key_moments=[])
NUDGE = CoachReply(text="What can your opponent do next?", fact_ids=[])


def ask(harness: Harness | ActiveHarness, text: str = QUESTION) -> int:
    response = harness.api.post(f"/games/{harness.game_id}/coach/messages", json={"text": text})
    assert harness.api.portal is not None
    harness.api.portal.call(harness.app.state.passive_coach.wait_until_idle)
    return response.status_code


def stored(harness: Harness | ActiveHarness) -> list[tuple[int, str, str]]:
    with Session(harness.app.state.engine) as session:
        rows = session.scalars(select(CoachMessage).order_by(CoachMessage.id))
        return [(m.ply, m.role.value, m.kind.value) for m in rows]


def test_a_question_without_an_open_one_gets_a_grounded_reply_about_the_position(
    settings: Settings,
) -> None:
    board = chess.Board()
    for san in ("e4", "e5", "Nf3", "Nc6"):
        board.push_san(san)
    cited = [fact.id for fact in build_fact_packet(board).facts[:1]]
    reply = CoachReply(text="Keep an eye on the centre.", fact_ids=cited)
    model = scripted(reply)
    with started(settings, model) as harness:
        harness.play(2)
        harness.events()

        assert ask(harness) == 202

        assert stored(harness) == [(4, "player", "asked"), (4, "coach", "answer")]
        [event] = [e for e in harness.events() if isinstance(e, CoachCommentEvent)]
        assert (event.kind.value, event.ply, event.text) == ("answer", 4, reply.text)
    [call] = model.calls
    payload = json.loads(call.messages[0].content)
    assert payload["event"] == "answer_question" and payload["player_message"] == QUESTION
    assert payload["fen"] == build_fact_packet(board).fen and payload["locked_in"] is True


def test_the_conversation_history_keeps_the_question_and_the_reply(settings: Settings) -> None:
    board = chess.Board()
    for san in ("e4", "e5", "Nf3", "Nc6"):
        board.push_san(san)
    reply = CoachReply(
        text="Keep an eye on the centre.",
        fact_ids=[fact.id for fact in build_fact_packet(board).facts[:1]],
    )
    with started(settings, scripted(reply)) as harness:
        harness.play(2)
        ask(harness)

        history = harness.api.get(f"/games/{harness.game_id}/coach/messages").json()

        assert [(m["role"], m["kind"], m["text"]) for m in history] == [
            ("player", "asked", QUESTION),
            ("coach", "answer", reply.text),
        ]
        assert {m["phase"] for m in history} == {"opening"}


def test_a_question_needs_a_tutor_mode_and_a_running_game_and_some_text(
    settings: Settings,
) -> None:
    with started(settings, scripted(REPLY), mode="regular") as harness:
        assert ask(harness) == 409
    with started(settings, scripted(REPLY)) as harness:
        game = f"/games/{harness.game_id}/coach/messages"
        assert harness.api.post(game, json={"text": "  "}).status_code == 422
        harness.api.post(f"/games/{harness.game_id}/resign")
        assert ask(harness) == 409
        assert stored(harness) == []


def test_without_a_language_model_the_question_is_kept_and_nothing_replies(
    settings: Settings,
) -> None:
    app = create_app(settings, ScriptedOpponent("e5"), None)
    with TestClient(app) as api:
        game_id = api.post("/games", json={"mode": "passive", "color": "white"}).json()["id"]

        response = api.post(f"/games/{game_id}/coach/messages", json={"text": QUESTION})
        assert api.portal is not None
        api.portal.call(app.state.passive_coach.wait_until_idle)

        assert response.status_code == 202
        with Session(app.state.engine) as session:
            [message] = session.scalars(select(CoachMessage)).all()
        assert (message.ply, message.kind.value) == (0, "asked")


def test_an_open_thought_question_is_still_answered_not_asked(settings: Settings) -> None:
    from test_thought_questions import ANSWER, asking
    from test_thought_questions import REPLY as ANSWER_REPLY

    with started(asking(settings), scripted(ANSWER_REPLY)) as harness:
        harness.play(4)
        ask(harness, ANSWER)

        assert stored(harness) == [
            (7, "coach", "question"),
            (7, "player", "answer"),
            (7, "coach", "answer"),
        ]


def test_an_active_question_follows_the_hint_ladder_and_never_names_the_best_move(
    settings: Settings,
) -> None:
    best = best_move_before_blunder(settings)
    settings = settings.model_copy(update={"active_remark_every_n_proposals": 1000})
    model = ScriptedLanguageModel(PRICED)
    model.queue_structured(NUDGE)  # the hint for the first proposal
    model.queue_structured(CoachReply(text=f"Look at {best}.", fact_ids=["allows:c6e5"]))
    model.queue_structured(
        CoachReply(text=NUDGE.text, fact_ids=["allows:c6e5"])
    )  # the rewrite, after the leak was rejected
    model.queue_structured(REPLY)  # the hint for the second proposal
    model.queue_structured(REPLY)  # the answer, one rethink later
    with started_active(settings, model) as harness:
        harness.play_setup()
        harness.propose(BLUNDER)

        ask(harness)

        harness.post("moves/rethink")
        harness.propose(BLUNDER)
        ask(harness, "Why is that bad?")

        messages = stored(harness)
        with Session(harness.app.state.engine) as session:
            texts = [
                m.text for m in session.scalars(select(CoachMessage).order_by(CoachMessage.id))
            ]
            sources = [m.source.value for m in session.scalars(select(CoachMessage))]
    assert (6, "player", "asked") in messages and (6, "coach", "answer") in messages
    assert all(best not in text for text in texts)
    assert "llm_regenerated" in sources  # the first answer named the best move and was rewritten
    asked = [json.loads(call.messages[0].content) for call in model.calls]
    answers = [p for p in asked if p["event"] == "answer_question"]
    assert [p["hint_level"] for p in answers] == [1, 1, 2]  # the rewrite repeats the first call
    for payload in answers:
        assert payload["locked_in"] is False
        assert not {"best_move", "best_line"} & payload.keys()
        assert best not in payload["allowed_references"]["moves"]


def test_an_active_question_before_any_proposal_starts_at_the_nudge(settings: Settings) -> None:
    best = best_move_before_blunder(settings)
    settings = settings.model_copy(update={"active_remark_every_n_proposals": 1000})
    model = ScriptedLanguageModel(PRICED)
    model.queue_structured(NUDGE)
    with started_active(settings, model) as harness:
        harness.play_setup()

        ask(harness)

    [payload] = [json.loads(call.messages[0].content) for call in model.calls]
    assert payload["hint_level"] == 1 and payload["locked_in"] is False
    assert best not in json.dumps(payload["facts"]) and "best_move" not in payload


def finished_game(harness: Harness) -> None:
    """Three moves, a resignation, and the background summary written."""
    harness.play(3)
    harness.api.post(f"/games/{harness.game_id}/resign")
    assert harness.api.portal is not None
    harness.api.portal.call(harness.app.state.passive_coach.wait_until_idle)


def best_move_after(settings: Settings, plies: list[str]) -> tuple[str, str]:
    board = chess.Board()
    for san in plies:
        board.push_san(san)

    async def best() -> str:
        async with StockfishPool(settings.stockfish_path, 1) as pool:
            [line, *_] = await pool.analyse(board, Budget.STANDARD)
        return san_line(board, line.pv[:1])[0]

    return asyncio.run(best()), board.fen()


def test_a_review_question_can_reveal_the_best_move_of_the_shown_position(
    settings: Settings,
) -> None:
    best, fen = best_move_after(settings, ["e4", "e5", "Nf3", "Nc6", "Bc4", "Nf6"])
    packet_ids = [fact.id for fact in build_fact_packet(chess.Board(fen)).facts[:1]]
    reply = CoachReply(text=f"The strongest idea here is {best}.", fact_ids=packet_ids)
    model = scripted(DRAFT)
    with started(settings, model) as harness:
        finished_game(harness)
        model.queue_structured(reply)
        url = f"/games/{harness.game_id}/review/questions"

        response = harness.api.post(url, json={"ply": 6, "text": "What was best here?"})

        assert response.status_code == 200
        assert (response.json()["text"], response.json()["source"]) == (reply.text, "llm")
        assert best in response.json()["text"]
        assert stored(harness) == [(6, "player", "asked"), (6, "coach", "answer")]
    payload = json.loads(model.calls[-1].messages[0].content)
    assert payload["locked_in"] is True and payload["best_move"] == best
    assert payload["fen"] == fen


def test_a_review_question_is_for_finished_games_and_real_plies(settings: Settings) -> None:
    with started(settings, scripted(DRAFT, REPLY)) as harness:
        url = f"/games/{harness.game_id}/review/questions"
        assert harness.api.post(url, json={"ply": 0, "text": QUESTION}).status_code == 409
        finished_game(harness)
        assert harness.api.post(url, json={"ply": 99, "text": QUESTION}).status_code == 422
        assert harness.api.post(url, json={"ply": 0, "text": " "}).status_code == 422
        assert (
            harness.api.post(
                "/games/999/review/questions", json={"ply": 0, "text": "x"}
            ).status_code
            == 404
        )
        assert stored(harness) == []


def test_a_review_question_without_a_language_model_is_a_503(settings: Settings) -> None:
    app = create_app(settings, ScriptedOpponent("e5"), None)
    with TestClient(app) as api:
        game_id = api.post("/games", json={"mode": "passive", "color": "white"}).json()["id"]
        api.post(f"/games/{game_id}/resign")

        response = api.post(f"/games/{game_id}/review/questions", json={"ply": 0, "text": "x"})

        assert response.status_code == 503
        with Session(app.state.engine) as session:
            assert session.scalars(select(CoachMessage)).all() == []
            assert session.scalars(select(Game)).one().llm_cost == 0


def test_an_answer_built_for_a_taken_back_line_is_not_stored_for_the_new_one(
    settings: Settings,
) -> None:
    board = chess.Board()
    for san in ("e4", "e5"):
        board.push_san(san)
    cited = [fact.id for fact in build_fact_packet(board).facts[:1]]
    model = GatedLanguageModel()
    model.queue_structured(CoachReply(text="Keep an eye on the centre.", fact_ids=cited))
    with started(settings, model) as harness:
        game = f"/games/{harness.game_id}"
        harness.api.post(f"{game}/moves", json={"uci": "e2e4"})
        harness.api.post(f"{game}/coach/messages", json={"text": QUESTION})
        assert harness.api.portal is not None
        harness.api.portal.call(model.asked.wait)

        harness.api.post(f"{game}/takeback")
        harness.api.post(f"{game}/moves", json={"uci": "d2d4"})  # a different line, same length
        harness.api.portal.call(model.release.set)
        harness.api.portal.call(harness.app.state.passive_coach.wait_until_idle)

        assert harness.events() == [] or all(
            not isinstance(e, CoachCommentEvent) or e.kind.value != "answer"
            for e in harness.events()
        )
        assert ("coach", "answer") not in [(r, k) for _, r, k in stored(harness)]
        with Session(harness.app.state.engine) as session:
            assert session.get_one(Game, harness.game_id).llm_cost > 0  # the call was billed


def test_a_question_after_a_rethink_keeps_the_rethink_count(settings: Settings) -> None:
    settings = settings.model_copy(update={"active_remark_every_n_proposals": 1000})
    model = ScriptedLanguageModel(PRICED)
    model.queue_structured(NUDGE)  # the hint for the proposal
    board = chess.Board()
    for san in ("e4", "e5", "Nf3", "Nc6", "Bc4", "Nf6"):
        board.push_san(san)
    cited = [fact.id for fact in build_fact_packet(board).facts[:1]]
    model.queue_structured(CoachReply(text="Think about the centre.", fact_ids=cited))
    with started_active(settings, model) as harness:
        harness.play_setup()
        harness.propose(BLUNDER)
        harness.post("moves/rethink")

        ask(harness)

    payloads = [json.loads(call.messages[0].content) for call in model.calls]
    assert [p["hint_level"] for p in payloads if p["event"] == "answer_question"] == [2]


class AnswerGatedLanguageModel(ScriptedLanguageModel):
    """Holds only the answers to the player's questions."""

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
        if '"event":"answer_question"' in messages[0].content:
            self.asked.set()
            await self.release.wait()
        return await super().structured(tier=tier, system=system, messages=messages, schema=schema)


def test_an_answer_about_a_rethought_proposal_is_not_stored(settings: Settings) -> None:
    settings = settings.model_copy(update={"active_remark_every_n_proposals": 1000})
    model = AnswerGatedLanguageModel()
    for _ in range(4):
        model.queue_structured(NUDGE)
    with started_active(settings, model) as harness:
        harness.play_setup()
        harness.propose(BLUNDER)
        harness.api.post(f"/games/{harness.game_id}/coach/messages", json={"text": QUESTION})
        assert harness.api.portal is not None
        harness.api.portal.call(model.asked.wait)

        harness.post("moves/rethink")
        harness.propose("d2d4")  # the board is unchanged, the proposal is not
        harness.api.portal.call(model.release.set)
        harness.api.portal.call(harness.app.state.passive_coach.wait_until_idle)

        assert ("coach", "answer") not in [(r, k) for _, r, k in stored(harness)]
        with Session(harness.app.state.engine) as session:
            assert session.get_one(Game, harness.game_id).llm_cost > 0


def test_the_review_shows_a_game_question_on_the_ply_it_was_asked_about(
    settings: Settings,
) -> None:
    board = chess.Board()
    for san in ("e4", "e5", "Nf3", "Nc6"):
        board.push_san(san)
    cited = [fact.id for fact in build_fact_packet(board).facts[:1]]
    model = scripted(DRAFT, CoachReply(text="Keep an eye on the centre.", fact_ids=cited))
    with started(settings, model) as harness:
        harness.play(2)
        ask(harness)
        harness.api.post(f"/games/{harness.game_id}/resign")
        assert harness.api.portal is not None
        harness.api.portal.call(harness.app.state.passive_coach.wait_until_idle)

        history = harness.api.get(f"/games/{harness.game_id}/coach/messages").json()
        assert [(m["ply"], m["kind"]) for m in history] == [(4, "asked"), (4, "answer")]
