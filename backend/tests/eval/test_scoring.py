import json
import re

import chess
import pytest
from eval_scoring import (
    CaseScore,
    EvalCase,
    build_context,
    concept_mentioned,
    forbidden_claims,
    load_cases,
    score_case,
    summarize,
)

from chess_tutor.analysis.move_delta import move_delta
from chess_tutor.coach.grounding import GroundedReply, ReplySource
from chess_tutor.coach.llm import Usage
from chess_tutor.coach.prompt import CoachContext, CoachEvent, build_coach_request
from chess_tutor.engines.stockfish import StockfishPool
from chess_tutor.facts.models import FactType
from chess_tutor.facts.packet import build_fact_packet
from chess_tutor.models import GameMode
from chess_tutor.openings.phase import GamePhase
from chess_tutor.settings import Settings

FORK_POSITION = "r3k3/8/8/3N4/8/8/8/4K3 w - - 0 1"


def context(*, pre_lock_in: bool = True, hint_level: int | None = None) -> CoachContext:
    return CoachContext(
        mode=GameMode.ACTIVE,
        active_pre_lock_in=pre_lock_in,
        phase=GamePhase.ENDGAME,
        event=CoachEvent.HINT,
        move_san="Kf2",
        fact_packet=build_fact_packet(chess.Board(FORK_POSITION)),
        best_move_san="Nc7+",
        best_line=["Nc7+", "Kd7", "Nxa8"],
        hint_level=hint_level,
    )


def test_a_fact_type_expectation_is_met_by_a_cited_fact_of_that_type() -> None:
    assert concept_mentioned(["fork"], "Look closer.", {FactType.FORK})
    assert not concept_mentioned(["fork"], "Look closer.", {FactType.PIN})


def test_a_text_expectation_is_a_case_insensitive_regex_and_any_one_suffices() -> None:
    assert concept_mentioned(["fork", "knight|rook"], "Watch that KNIGHT.", set())
    assert not concept_mentioned(["fork", "knight|rook"], "Watch that bishop.", set())


def test_no_expectation_is_vacuously_met() -> None:
    assert concept_mentioned([], "anything", set())


def test_naming_the_best_move_before_lock_in_is_forbidden() -> None:
    assert "best_move:Nc7+" in forbidden_claims("Try Nc7+ here.", context(), [])
    assert "best_move:Nc7+" in forbidden_claims("Try Nc7 here.", context(), [])
    assert "best_move:Nc7+" not in forbidden_claims("Try Kf2 here.", context(), [])


def test_the_best_move_is_fine_after_lock_in_or_when_it_is_the_proposal() -> None:
    assert forbidden_claims("Nc7+ was strong.", context(pre_lock_in=False), []) == []
    proposal = context().model_copy(update={"move_san": "Nc7+"})
    assert forbidden_claims("Nc7+ is your idea.", proposal, []) == []


def test_a_square_outside_the_facts_is_forbidden_and_one_inside_is_not() -> None:
    assert forbidden_claims("Think about h6.", context(pre_lock_in=False), []) == [
        "unknown_square:h6"
    ]
    assert forbidden_claims("Think about c7.", context(pre_lock_in=False), []) == []


def test_a_piece_the_board_does_not_show_on_a_square_is_forbidden() -> None:
    assert forbidden_claims("The bishop on e8 matters.", context(pre_lock_in=False), []) == [
        "false_piece:bishop on e8"
    ]
    assert forbidden_claims("The king on e8 matters.", context(pre_lock_in=False), []) == []


def test_level_one_names_no_square_and_level_two_at_most_one() -> None:
    assert "square_in_level_1:c7" in forbidden_claims("Watch c7.", context(hint_level=1), [])
    assert "too_many_squares_in_level_2" in forbidden_claims(
        "Watch c7 and a8.", context(hint_level=2), []
    )
    assert "too_many_squares_in_level_2" not in forbidden_claims(
        "Watch c7.", context(hint_level=2), []
    )
    assert not forbidden_claims("What can your opponent do next?", context(hint_level=1), [])


def test_the_squares_of_the_students_own_move_are_always_fair_to_name() -> None:
    reply = "Moving from e1 to f2, watch c7."

    assert "too_many_squares_in_level_2" not in forbidden_claims(reply, context(hint_level=2), [])
    assert forbidden_claims("Moving to f2.", context(hint_level=2), []) == []
    queen_leaves = context(pre_lock_in=False).model_copy(
        update={
            "move_san": "Qg4",
            "fact_packet": build_fact_packet(
                chess.Board("3qk2r/pp3ppp/8/8/1n6/8/PP3PPP/R2QK1N1 w - - 0 1")
            ),
        }
    )
    assert forbidden_claims("Your queen leaves d1 for g4.", queen_leaves, []) == []
    assert "unknown_square:d2" in forbidden_claims("Your queen skips d2.", queen_leaves, [])


@pytest.mark.parametrize(
    "text",
    ["Good catch, here is a better one.", "Sorry, let me redo that.", "The engine says so."]
    + ["According to the facts, it is fine.", "Rewriting my previous reply."],
)
def test_correction_and_data_meta_talk_is_forbidden(text: str) -> None:
    assert forbidden_claims(text, context(pre_lock_in=False), []) == ["meta_talk"]


def test_a_case_specific_forbidden_pattern_is_reported() -> None:
    assert forbidden_claims("A nasty fork looms.", context(pre_lock_in=False), ["fork"]) == [
        "forbidden_phrase:fork"
    ]


def test_every_curated_case_is_a_legal_position_with_a_legal_move() -> None:
    cases = load_cases()

    assert len(cases) >= 30 and len({case.id for case in cases}) == len(cases)
    assert {case.phase for case in cases} == set(GamePhase)
    for case in cases:
        board = chess.Board(case.fen)
        assert board.parse_san(case.move), case.id


def test_the_cases_cover_every_mode_and_event() -> None:
    cases = load_cases()

    def modes(event: CoachEvent) -> set[GameMode]:
        return {case.mode for case in cases if case.event is event}

    assert modes(CoachEvent.COMMENT_ON_MOVE) == {GameMode.PASSIVE}
    assert {case.hint_level for case in cases if case.event is CoachEvent.HINT} == {1, 2, 3}
    assert modes(CoachEvent.ANSWER_QUESTION) == {GameMode.PASSIVE, GameMode.ACTIVE}
    assert all(case.player_message for case in cases if case.event is CoachEvent.ANSWER_QUESTION)


def test_an_active_case_is_pre_lock_in_unless_it_says_it_is_locked_in() -> None:
    base = {
        "id": "x", "phase": "opening", "mode": "active", "event": "hint",
        "fen": chess.STARTING_FEN, "move": "e4", "expect_any": [], "forbid": [],
    }  # fmt: skip

    assert EvalCase.model_validate(base).active_pre_lock_in
    assert not EvalCase.model_validate(base | {"locked_in": True}).active_pre_lock_in
    assert not EvalCase.model_validate(base | {"mode": "passive"}).active_pre_lock_in


def test_a_case_scores_each_dimension_separately() -> None:
    case = EvalCase.model_validate(
        {
            "id": "x", "phase": "endgame", "mode": "active", "event": "hint", "locked_in": True,
            "fen": FORK_POSITION, "move": "Kf2", "expect_any": ["fork"], "forbid": [],
        }
    )  # fmt: skip
    usage = Usage(model="claude-sonnet-5-5", input_tokens=1_000_000)
    fork = next(f.id for f in build_fact_packet(chess.Board(FORK_POSITION)).facts if "fork" in f.id)

    def scored(text: str, source: ReplySource, *fact_ids: str) -> CaseScore:
        grounded = GroundedReply(text, list(fact_ids), source, (usage,))
        return score_case(case, context(pre_lock_in=False), grounded, 1.5)

    good = scored("Nc7+ forks the king on e8.", ReplySource.LLM, fork)
    assert good.passes and good.cost == pytest.approx(2.0) and good.seconds == 1.5

    regenerated = scored("Nc7+ forks the king on e8.", ReplySource.LLM_REGENERATED, fork)
    assert not regenerated.grounded_first_attempt and regenerated.validator_passes
    assert not scored("Think about h6.", ReplySource.LLM).no_forbidden_claim
    assert not scored("Nice move.", ReplySource.LLM).concept_mentioned
    assert not scored("Look at Nh6.", ReplySource.LLM, fork).validator_passes


def test_the_summary_reports_rates_cost_and_failing_cases() -> None:
    def score(case_id: str, grounded: bool) -> CaseScore:
        return CaseScore(case_id, grounded, True, True, True, [], "text", 0.25, 2.0)

    summary = summarize([score("a", True), score("b", False)])

    assert "1/2 cases passed all dimensions" in summary
    assert "grounded on first attempt: 1/2 (50%)" in summary
    assert "passes validator: 2/2 (100%)" in summary
    assert "total cost: $0.5000" in summary and "total time: 4s" in summary
    assert "FAIL b: grounded on first attempt" in summary and "FAIL a" not in summary


def test_a_forbid_pattern_matches_whole_words_only() -> None:
    case = next(case for case in load_cases() if case.id == "hint3-opening-missed-mate")
    pattern = case.forbid[0]

    assert re.search(pattern, "That is checkmate.", re.IGNORECASE)
    assert re.search(pattern, "There is a mate.", re.IGNORECASE)
    assert not re.search(pattern, "You are ahead in material.", re.IGNORECASE)
    assert not forbidden_claims("You are ahead in material.", context(pre_lock_in=False), [pattern])


def test_a_disambiguated_move_is_not_read_as_a_bare_square() -> None:
    assert "unknown_square:d7" not in forbidden_claims("Nbd7 is fine.", context(), [])


@pytest.mark.parametrize("case_id", ["hint3-opening-missed-mate", "passive-middlegame-allows-fork"])
async def test_a_real_case_builds_a_context_and_the_expected_request_payload(case_id: str) -> None:
    case = next(case for case in load_cases() if case.id == case_id)
    board = chess.Board(case.fen)
    async with StockfishPool(Settings(_env_file=None).stockfish_path, 1) as pool:
        delta = await move_delta(board, board.parse_san(case.move), pool)

    context = build_context(case, delta)
    system, messages = build_coach_request(context)
    payload = json.loads(messages[0].content)

    assert system and context.move_san == case.move and context.fact_packet.fen == board.fen()
    assert payload["event"] == case.event.value and payload["mode"] == case.mode.value
    assert payload["locked_in"] is (not case.active_pre_lock_in)
    assert payload["facts"] and set(payload["allowed_references"]) == {"squares", "moves"}
    assert ("best_move" in payload) is (not case.active_pre_lock_in)
    assert payload.get("hint_level") == case.hint_level
