from sqlalchemy import select
from sqlalchemy.orm import Session
from test_passive_coach import COMMENT, GENERIC, UNGROUNDED, Harness, scripted, started

from chess_tutor.api.events import CoachCommentEvent
from chess_tutor.coach.prompt import CoachReply
from chess_tutor.models import CoachMessage
from chess_tutor.settings import Settings

ANSWER = "I thought my knight could win a pawn."
REPLY = CoachReply(
    text=(
        "I see the idea, but Nxe5 lets Black recapture with Nxe5, "
        "and then your bishop on c4 hangs."
    ),
    fact_ids=["allows:c6e5"],
)


def asking(settings: Settings, every_n_moves: int = 4) -> Settings:
    return settings.model_copy(update={"thought_question_every_n_moves": every_n_moves})


def stored(harness: Harness) -> list[tuple[int, str, str]]:
    return [(m.ply, m.role.value, m.kind.value) for m in harness.comments()]


def answer(harness: Harness, text: str = ANSWER) -> int:
    response = harness.api.post(f"/games/{harness.game_id}/coach/messages", json={"text": text})
    assert harness.api.portal is not None
    harness.api.portal.call(harness.app.state.passive_coach.wait_until_idle)
    return response.status_code


def test_a_big_drop_asks_what_the_player_thought_and_routine_moves_do_not(
    settings: Settings,
) -> None:
    model = scripted()
    with started(asking(settings), model) as harness:
        harness.play(3)
        assert harness.comments() == []

        harness.play(1)

        assert stored(harness) == [(7, "coach", "question")]
        [event] = [e for e in harness.events() if isinstance(e, CoachCommentEvent)]
        assert (event.kind.value, event.ply, event.classification) == ("question", 7, "blunder")
        [message] = harness.api.get(f"/games/{harness.game_id}/coach/messages").json()
        assert message["kind"] == "question" and "Nxe5" in message["text"]
    assert model.calls == []  # the question costs no LLM call


def test_questions_are_off_when_the_spacing_is_zero(settings: Settings) -> None:
    with started(asking(settings, 0), scripted(COMMENT)) as harness:
        harness.play(4)

        assert stored(harness) == [(7, "coach", "comment")]


def test_skipping_closes_the_question_and_asks_no_follow_up(settings: Settings) -> None:
    with started(asking(settings), scripted(GENERIC)) as harness:
        harness.play(4)
        game = f"/games/{harness.game_id}/coach"

        assert harness.api.post(f"{game}/question/skip").status_code == 204
        assert harness.api.post(f"{game}/question/skip").status_code == 409
        harness.play(1)  # a second key moment, but inside the spacing

        assert stored(harness) == [
            (7, "coach", "question"),
            (7, "player", "skipped"),
            (9, "coach", "comment"),
        ]


def test_an_unanswered_question_blocks_the_next_one(settings: Settings) -> None:
    with started(asking(settings, 1), scripted(GENERIC)) as harness:
        harness.play(5)

        assert [kind for _, _, kind in stored(harness)].count("question") == 1


def test_a_second_key_moment_after_the_spacing_asks_again(settings: Settings) -> None:
    with started(asking(settings, 1), scripted(REPLY)) as harness:
        harness.play(4)
        assert answer(harness) == 202
        harness.play(1)

        assert [(ply, kind) for ply, _, kind in stored(harness) if kind == "question"] == [
            (7, "question"),
            (9, "question"),
        ]


def test_an_answer_gets_a_grounded_reply_at_the_same_ply(settings: Settings) -> None:
    model = scripted(REPLY)
    with started(asking(settings), model) as harness:
        harness.play(4)
        harness.events()

        assert answer(harness) == 202

        assert stored(harness) == [
            (7, "coach", "question"),
            (7, "player", "answer"),
            (7, "coach", "answer"),
        ]
        reply = harness.comments()[-1]
        assert (reply.text, reply.source.value, reply.fact_ids) == (
            REPLY.text,
            "llm",
            '["allows:c6e5"]',
        )
        [event] = [e for e in harness.events() if isinstance(e, CoachCommentEvent)]
        assert (event.kind.value, event.ply, event.text) == ("answer", 7, REPLY.text)
    [call] = model.calls
    assert '"event":"answer_question"' in call.messages[0].content
    assert ANSWER in call.messages[0].content
    assert '"locked_in":true' in call.messages[0].content


def test_an_ungrounded_reply_to_an_answer_is_regenerated(settings: Settings) -> None:
    with started(asking(settings), scripted(UNGROUNDED, REPLY)) as harness:
        harness.play(4)
        answer(harness)

        last = harness.comments()[-1]
        assert (last.text, last.source.value) == (REPLY.text, "llm_regenerated")


def test_an_answer_needs_a_pending_question_and_some_text(settings: Settings) -> None:
    with started(asking(settings), scripted(REPLY)) as harness:
        game = f"/games/{harness.game_id}/coach/messages"
        assert harness.api.post(game, json={"text": ANSWER}).status_code == 409
        harness.play(4)

        assert harness.api.post(game, json={"text": "  "}).status_code == 422
        assert harness.api.post(game, json={"text": ANSWER}).status_code == 202
        assert harness.api.post(game, json={"text": ANSWER}).status_code == 409
        assert harness.api.post("/games/999/coach/messages", json={"text": "x"}).status_code == 404


def test_the_review_pairs_the_answer_with_the_coach_reply(settings: Settings) -> None:
    with started(asking(settings), scripted(REPLY)) as harness:
        harness.play(4)
        answer(harness)
        harness.api.post(f"/games/{harness.game_id}/resign")

        body = harness.api.get(f"/games/{harness.game_id}/review").json()

        assert body["reasoning"] == [{"ply": 7, "answer": ANSWER, "coach_reply": REPLY.text}]


def test_taking_the_move_back_removes_its_question(settings: Settings) -> None:
    with started(asking(settings), scripted(REPLY)) as harness:
        harness.play(4)
        harness.api.post(f"/games/{harness.game_id}/takeback")

        game = f"/games/{harness.game_id}/coach/messages"
        assert harness.api.post(game, json={"text": ANSWER}).status_code == 409
        with Session(harness.app.state.engine) as session:
            assert session.scalars(select(CoachMessage)).all() == []
