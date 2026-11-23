import re
from collections.abc import AsyncIterator, Callable, Sequence

import anthropic
import chess
import pytest
from pydantic import BaseModel
from scripted_language_model import ScriptedLanguageModel

from chess_tutor.analysis.move_delta import move_delta
from chess_tutor.coach.grounding import (
    LEVEL_ONE_NUDGE,
    GroundedReply,
    GroundingErrorKind,
    ReplySource,
    coach_reply_with_grounding,
    template_reply,
    validate_reply,
)
from chess_tutor.coach.llm import (
    AnthropicLanguageModel,
    ChatMessage,
    LanguageModelError,
    ModelTier,
    StructuredReply,
    Usage,
)
from chess_tutor.coach.prompt import CoachContext, CoachEvent, CoachReply
from chess_tutor.engines.stockfish import StockfishPool
from chess_tutor.facts.models import Fact, FactPacket, FactType, PieceRef, Side
from chess_tutor.facts.packet import build_fact_packet
from chess_tutor.models import GameMode
from chess_tutor.openings.phase import GamePhase
from chess_tutor.settings import Settings

FORK_ON_C7 = "r3k3/8/8/3N4/8/8/8/4K3 w - - 0 1"
FORK = "fork_opportunity:d5c7"
USAGE = Usage(model="claude-sonnet-5-5", input_tokens=100, output_tokens=20)


def context_for(**overrides: object) -> CoachContext:
    values: dict[str, object] = {
        "mode": GameMode.ACTIVE,
        "phase": GamePhase.MIDDLEGAME,
        "event": CoachEvent.COMMENT_ON_MOVE,
        "move_san": "Kf2",
        "fact_packet": build_fact_packet(chess.Board(FORK_ON_C7)),
        "best_move_san": "Nc7+",
        "best_line": ["Nc7+", "Kd7", "Nxa8"],
        "active_pre_lock_in": False,
    }
    return CoachContext.model_validate(values | overrides)


def with_facts(*facts: Fact, **overrides: object) -> CoachContext:
    packet = build_fact_packet(chess.Board(FORK_ON_C7))
    return context_for(
        fact_packet=FactPacket(fen=packet.fen, side_to_move=packet.side_to_move, facts=list(facts)),
        **overrides,
    )


NO_BEST: dict[str, object] = {"best_move_san": None, "best_line": []}


def kinds(reply: CoachReply, context: CoachContext) -> set[GroundingErrorKind]:
    return {error.kind for error in validate_reply(reply, context)}


def reply(text: str, *fact_ids: str) -> CoachReply:
    return CoachReply(text=text, fact_ids=list(fact_ids))


def test_a_reply_using_only_cited_facts_passes() -> None:
    text = "Nice spot: Nc7+ would fork the rook on a8 and the king on e8."

    assert validate_reply(reply(text, FORK), context_for()) == []


def test_general_language_with_no_move_or_square_passes() -> None:
    assert (
        validate_reply(reply("Think about what a rook can do on an open file."), context_for())
        == []
    )


ALLOWS_FORK = Fact(
    id="allows:b4c2",
    type=FactType.ALLOWS,
    side=Side.BLACK,
    summary="the move allows Nc2+",
    squares=["b4"],
    pieces=[PieceRef(color=Side.BLACK, piece_type="knight", square="b4")],
    san_lines=[["Kd7", "Nb6+", "Kc7"]],
)


def test_squares_a_verified_line_moves_to_or_from_are_grounded() -> None:
    context = with_facts(ALLOWS_FORK, **NO_BEST)

    text = "After Kd7 the knight can reach b6 with check, and the king ends on c7 from d7."

    assert validate_reply(reply(text, "allows:b4c2"), context) == []
    assert GroundingErrorKind.UNCITED_SQUARE in kinds(reply("Watch h6.", "allows:b4c2"), context)


def test_check_marks_and_plural_piece_names_are_not_false_positives() -> None:
    context = with_facts(ALLOWS_FORK, **NO_BEST)

    text = "Kd7+ then Nb6 and the knights on b4 or b6 are busy; you may castle too, or O-O."

    assert kinds(reply(text, "allows:b4c2"), context) == {GroundingErrorKind.UNVERIFIED_MOVE}
    assert (
        validate_reply(
            reply(text.replace("; you may castle too, or O-O", ""), "allows:b4c2"), context
        )
        == []
    )


def test_ordinary_prose_with_move_like_words_passes() -> None:
    text = "A solid plan: Be careful, Re-think, and Keep the king safe; a1 b2 are not mentioned."

    assert kinds(reply(text), context_for()) == {GroundingErrorKind.UNCITED_SQUARE}
    assert (
        validate_reply(
            reply("Be careful. Re-think it. Rest a bit, then Keep going."), context_for()
        )
        == []
    )


def test_the_move_being_commented_on_needs_no_fact() -> None:
    assert (
        validate_reply(reply("Kf2 is a calm move, but keep looking around."), context_for()) == []
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("The knight on c7 attacks the rook on h8.", GroundingErrorKind.UNCITED_SQUARE),
        ("Look at b6, that is where it jumps.", GroundingErrorKind.UNCITED_SQUARE),
        ("Nc7+ and then Qxh7 wins.", GroundingErrorKind.UNVERIFIED_MOVE),
        ("Nxe5 grabs a pawn.", GroundingErrorKind.UNVERIFIED_MOVE),
        ("You could castle with O-O here.", GroundingErrorKind.UNVERIFIED_MOVE),
        ("The bishop on c7 forks two pieces.", GroundingErrorKind.PIECE_MISMATCH),
    ],
)
def test_hallucinated_text_is_rejected(text: str, expected: GroundingErrorKind) -> None:
    assert expected in kinds(reply(text, FORK), context_for())


def test_an_uncited_claim_is_rejected() -> None:
    assert GroundingErrorKind.UNVERIFIED_MOVE in kinds(
        reply("Nc7+ forks two pieces."), context_for(**NO_BEST)
    )
    assert GroundingErrorKind.UNCITED_SQUARE in kinds(
        reply("Watch the rook on a8."), context_for(**NO_BEST)
    )


def test_a_square_from_a_fact_that_was_not_cited_is_rejected() -> None:
    text = "Your king on e1 is a long way from the centre."

    context = context_for(move_san="Nb4", **NO_BEST)

    assert GroundingErrorKind.UNCITED_SQUARE in kinds(reply(text, FORK), context)
    assert validate_reply(reply(text, "activity:white:passive_king"), context) == []


def test_an_unknown_fact_id_is_rejected() -> None:
    assert kinds(reply("Watch the knight.", "made:up"), context_for()) == {
        GroundingErrorKind.UNKNOWN_FACT
    }


def test_a_san_whose_line_is_illegal_is_rejected() -> None:
    broken = Fact(
        id="broken", type=FactType.FORK, side=Side.WHITE, summary="x", san_lines=[["Nc7+", "Nc7"]]
    )
    context = with_facts(broken, **NO_BEST)

    assert GroundingErrorKind.UNVERIFIED_MOVE in kinds(reply("Nc7+ is strong.", "broken"), context)


def test_after_lock_in_the_best_move_and_line_are_citable() -> None:
    assert validate_reply(reply("Nc7+, Kd7 and Nxa8 wins the rook."), context_for()) == []


def test_the_best_move_is_rejected_before_lock_in() -> None:
    harmless = Fact(
        id="harmless", type=FactType.ALLOWS, side=Side.BLACK, summary="x", san_lines=[["Kd7"]]
    )
    context = with_facts(harmless, active_pre_lock_in=True)

    assert GroundingErrorKind.BEST_MOVE_LEAK in kinds(reply("Kd7, then Nc7+.", "harmless"), context)
    assert GroundingErrorKind.UNKNOWN_FACT in kinds(reply("Hmm.", FORK), context)  # filtered out
    assert validate_reply(reply("Kd7 is possible.", "harmless"), context) == []


@pytest.mark.parametrize("fact_type", list(FactType))
@pytest.mark.parametrize("with_geometry", [True, False])
@pytest.mark.parametrize("hint_level", [None, 1, 2, 3])
def test_the_template_validates_for_every_fact_type(
    fact_type: FactType, with_geometry: bool, hint_level: int | None
) -> None:
    reference = chess.Board(FORK_ON_C7)
    fact = Fact(
        id="template:fact",
        type=fact_type,
        side=Side.BLACK,
        summary="a summary naming a1, Qh5 and Nf3",
        severity=3,
        squares=["a8", "e8"] if with_geometry else [],
        pieces=(
            [
                PieceRef(color=Side.BLACK, piece_type="rook", square="a8"),
                PieceRef(color=Side.BLACK, piece_type="king", square="e8"),
            ]
            if with_geometry
            else []
        ),
        san_lines=[["Kd7", "Nb6+"]] if with_geometry else [],
    )
    context = with_facts(fact, hint_level=hint_level, active_pre_lock_in=True)

    template = template_reply(context)

    assert validate_reply(template, context) == []
    assert context.fact_packet.fen == reference.fen()
    if hint_level == 1:
        assert template.text == LEVEL_ONE_NUDGE
        assert template.fact_ids == []
    if hint_level in (1, 2):  # no consequence line: no SAN move, no "Careful" outcome
        assert not re.search(r"\b(?:[KQRBN][a-h]?x?[a-h][1-8]|Careful)", template.text)
    mentioned_squares = set(re.findall(r"\b[a-h][1-8]\b", template.text))
    assert len(mentioned_squares) <= {1: 0, 2: 1}.get(hint_level or 0, 99)


def test_the_template_for_an_allowed_reply_names_the_reply() -> None:
    allows = Fact(
        id="allows:e8d7",
        type=FactType.ALLOWS,
        side=Side.BLACK,
        summary="the move allows Kd7",
        severity=20,
        squares=["e8", "d7"],
        san_lines=[["Kd7"]],
    )
    context = with_facts(allows)

    template = template_reply(context)

    assert template.text == "Careful: after Kf2, your opponent has Kd7."
    assert validate_reply(template, context) == []


def test_the_template_for_an_ignored_threat_uses_the_opponents_turn() -> None:
    threat = Fact(
        id="ignored_threat:a8a1",
        type=FactType.IGNORED_THREAT,
        side=Side.BLACK,
        summary="black threatens Ra1+",
        severity=20,
        san_lines=[["Ra1+"]],
    )
    context = with_facts(threat)

    assert validate_reply(template_reply(context), context) == []


def test_the_template_never_reveals_the_best_move_before_lock_in() -> None:
    context = context_for(active_pre_lock_in=True, hint_level=3)

    template = template_reply(context)

    assert "Nc7" not in template.text
    assert validate_reply(template, context) == []


def test_level_two_names_at_most_one_square() -> None:
    template = template_reply(context_for(hint_level=2))

    assert len(set(template.text.split()) & {"a8", "e8", "c7", "e1"}) <= 1


def test_asking_for_a_thought_gets_a_question_and_no_facts() -> None:
    template = template_reply(context_for(event=CoachEvent.ASK_THOUGHT))

    assert template.text == "What were you thinking when you played Kf2?"
    assert template.fact_ids == []


def test_with_no_facts_the_template_is_generic() -> None:
    context = with_facts()

    assert validate_reply(template_reply(context), context) == []


class FailingLanguageModel:
    """Our own LanguageModel whose every call fails, as an unreachable provider would."""

    def __init__(self, usage: Usage | None) -> None:
        self._usage = usage

    async def structured[ParsedModel: BaseModel](
        self,
        *,
        tier: ModelTier,
        system: str,
        messages: Sequence[ChatMessage],
        schema: type[ParsedModel],
    ) -> StructuredReply[ParsedModel]:
        raise LanguageModelError("unreachable", self._usage)

    def stream_text(
        self,
        *,
        tier: ModelTier,
        system: str,
        messages: Sequence[ChatMessage],
        on_usage: Callable[[Usage], None] | None = None,
    ) -> AsyncIterator[str]:
        raise LanguageModelError("unreachable", self._usage)


GOOD = reply("Nc7+ forks the rook on a8 and the king on e8.", FORK)
BAD = reply("Nc7+ forks the rook on h8.", FORK)


async def test_a_valid_first_reply_is_used_as_is() -> None:
    model = ScriptedLanguageModel(USAGE)
    model.queue_structured(GOOD)

    result = await coach_reply_with_grounding(model, context_for())

    assert (result.text, result.fact_ids, result.source) == (GOOD.text, [FORK], ReplySource.LLM)
    assert len(model.calls) == 1


async def test_a_bad_first_reply_is_regenerated_with_the_errors_as_feedback() -> None:
    model = ScriptedLanguageModel(USAGE)
    model.queue_structured(BAD)
    model.queue_structured(GOOD)

    result = await coach_reply_with_grounding(model, context_for())

    assert result.source is ReplySource.LLM_REGENERATED
    assert result.text == GOOD.text
    assert result.usages == (USAGE, USAGE)
    assert result.cost == pytest.approx(2 * USAGE.cost)
    first, second = model.calls
    assert list(second.messages[:1]) == list(first.messages)
    assert [message.role for message in second.messages] == ["user", "system"]
    correction = second.messages[-1].content
    assert "h8, which is not in any cited fact" in correction
    assert BAD.text in correction
    assert "never refer to this correction" in correction


async def test_two_bad_replies_fall_back_to_a_valid_template() -> None:
    model = ScriptedLanguageModel(USAGE)
    model.queue_structured(BAD)
    model.queue_structured(reply("Qxh7 wins!", FORK))
    context = context_for()

    result = await coach_reply_with_grounding(model, context)

    assert result.source is ReplySource.TEMPLATE
    assert result.usages == (USAGE, USAGE)
    assert validate_reply(CoachReply(text=result.text, fact_ids=result.fact_ids), context) == []


async def test_a_model_error_falls_back_and_keeps_the_billed_usage() -> None:
    result = await coach_reply_with_grounding(FailingLanguageModel(USAGE), context_for())

    assert result.source is ReplySource.TEMPLATE
    assert result.usages == (USAGE,)


async def test_an_error_without_usage_records_none() -> None:
    result = await coach_reply_with_grounding(FailingLanguageModel(None), context_for())

    assert (result.source, result.usages) == (ReplySource.TEMPLATE, ())


KNIGHT_FORK_ALLOWED = ("3qk2r/pp3ppp/8/8/1n6/8/PP3PPP/R2QK1N1 w - - 0 1", "Qg4")
LONE_KING_MOVE = ("r3k3/8/8/3N4/8/8/8/4K3 b - - 0 1", "Kd7")
IGNORED_THREAT = ("rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2", "a6")
QUIET_GOOD_MOVE = ("rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2", "Nf3")
REAL_RUNS_PER_POSITION = 3


@pytest.mark.llm
async def test_real_coach_first_attempt_pass_rate_on_three_positions() -> None:
    settings = Settings(_env_file=None, llm_provider="anthropic")
    try:
        api_key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else None
        await anthropic.AsyncAnthropic(api_key=api_key).models.retrieve(settings.cheap_model)
    except (anthropic.AuthenticationError, TypeError):
        pytest.skip("no Anthropic credentials resolve (set ANTHROPIC_API_KEY or `ant auth login`)")
    model = AnthropicLanguageModel(settings)
    results: list[GroundedReply] = []
    async with StockfishPool(settings.stockfish_path, 1) as pool:
        for fen, san in (KNIGHT_FORK_ALLOWED, IGNORED_THREAT, QUIET_GOOD_MOVE, LONE_KING_MOVE):
            board = chess.Board(fen)
            delta = await move_delta(board, board.parse_san(san), pool)
            context = CoachContext(
                mode=GameMode.ACTIVE,
                phase=GamePhase.MIDDLEGAME,
                event=CoachEvent.COMMENT_ON_MOVE,
                move_san=san,
                fact_packet=build_fact_packet(board),
                move_delta_facts=delta.facts,
                best_move_san=delta.best_move_san,
                best_line=delta.best_line,
                active_pre_lock_in=False,
            )
            for _ in range(REAL_RUNS_PER_POSITION):
                grounded = await coach_reply_with_grounding(model, context)
                results.append(grounded)
                print(f"[{san}] {grounded.source.value}: {grounded.text} {grounded.fact_ids}")
                if grounded.first_attempt_errors:
                    print(f"    FIRST ATTEMPT TEXT: {grounded.first_attempt_text}")
                for error in grounded.first_attempt_errors:
                    print(f"    FIRST-ATTEMPT ERROR {error.kind.value}: {error.message}")
                assert not validate_reply(
                    CoachReply(text=grounded.text, fact_ids=grounded.fact_ids), context
                )
                words = set(re.findall(r"[a-z]+", grounded.text.lower()))
                assert not {"fact", "facts", "context"} & words
    first_attempt = sum(result.source is ReplySource.LLM for result in results)
    print(f"FIRST ATTEMPT PASSED: {first_attempt}/{len(results)}")


START = FactPacket(fen=chess.STARTING_FEN, side_to_move=Side.WHITE, facts=[])


def start_context(*facts: Fact, **overrides: object) -> CoachContext:
    packet = START.model_copy(update={"facts": list(facts)})
    return context_for(fact_packet=packet, move_san="Nf3", **NO_BEST | overrides)


E4_POINTS = Fact(
    id="e4:points",
    type=FactType.ACTIVITY,
    side=Side.WHITE,
    summary="x",
    squares=["e4"],
    pieces=[PieceRef(color=Side.WHITE, piece_type="pawn", square="e4")],
)
E4_LINE = Fact(
    id="e4:line", type=FactType.ACTIVITY, side=Side.WHITE, summary="x", san_lines=[["e4"]]
)


@pytest.mark.parametrize("text", ["Play e4.", "Then e4 is possible.", "Try e4 instead of Nf3."])
def test_a_bare_pawn_push_after_move_wording_needs_a_verified_line(text: str) -> None:
    context = start_context(E4_POINTS)

    assert GroundingErrorKind.UNVERIFIED_MOVE in kinds(reply(text, "e4:points"), context)


def test_a_square_mention_stays_a_square_check_and_a_verified_push_passes() -> None:
    assert (
        validate_reply(reply("Look at the pawn on e4.", "e4:points"), start_context(E4_POINTS))
        == []
    )
    assert validate_reply(reply("Play e4 now.", "e4:line"), start_context(E4_LINE)) == []
    assert GroundingErrorKind.UNCITED_SQUARE in kinds(
        reply("Look at the pawn on e4."), start_context(E4_POINTS)
    )


SQUARE_ONLY = Fact(
    id="d5:square", type=FactType.ACTIVITY, side=Side.WHITE, summary="x", squares=["d5"]
)


@pytest.mark.parametrize(
    ("text", "valid"),
    [
        ("The knight on d5 is strong.", True),
        ("The knights on d5 are strong.", True),
        ("The bishop on d5 is strong.", False),
        ("The d5 knight is strong.", True),
        ("Your d5 bishop is strong.", False),
        ("White's d5 pawn is strong.", False),
        ("The queen on d5 is strong.", False),
    ],
)
def test_piece_types_are_checked_against_the_board_when_no_fact_gives_one(
    text: str, valid: bool
) -> None:
    context = with_facts(SQUARE_ONLY, **NO_BEST)

    errors = kinds(reply(text, "d5:square"), context)

    assert (errors == set()) is valid
    assert valid or errors == {GroundingErrorKind.PIECE_MISMATCH}


def test_a_piece_on_a_square_reached_along_a_verified_line_is_checked_there() -> None:
    reached = Fact(
        id="reach", type=FactType.ALLOWS, side=Side.BLACK, summary="x", san_lines=[["Kd7", "Nb6+"]]
    )
    context = with_facts(reached, **NO_BEST)

    assert (
        validate_reply(reply("Kd7 then Nb6+, so the knight on b6 checks.", "reach"), context) == []
    )
    assert GroundingErrorKind.PIECE_MISMATCH in kinds(
        reply("Kd7 then Nb6+, so the rook on b6 checks.", "reach"), context
    )


def test_the_players_own_move_may_be_named_even_when_it_is_the_best_move() -> None:
    context = context_for(event=CoachEvent.ASK_THOUGHT, move_san="Nc7+", active_pre_lock_in=True)

    template = template_reply(context)

    assert "Nc7+" in template.text
    assert validate_reply(template, context) == []
    assert validate_reply(reply("Nc7+ was a fine try."), context) == []
    elsewhere = context.model_copy(update={"move_san": "Kf2"})
    assert GroundingErrorKind.BEST_MOVE_LEAK in kinds(reply("Nc7+ is it."), elsewhere)


def test_level_two_skips_the_tactical_template_for_allows() -> None:
    allows = Fact(
        id="allows:e8d7",
        type=FactType.ALLOWS,
        side=Side.BLACK,
        summary="x",
        squares=["e8", "d7"],
        pieces=[PieceRef(color=Side.BLACK, piece_type="king", square="e8")],
        san_lines=[["Kd7"]],
    )
    template = template_reply(with_facts(allows, hint_level=2, active_pre_lock_in=True))

    assert "Kd7" not in template.text
    assert "e8" in template.text


def test_a_verified_capture_on_a_cited_square_makes_the_new_occupant_valid() -> None:
    text = "After Nc7+, Kd7 and Nxa8, the knight on a8 has won the rook."

    assert validate_reply(reply(text, FORK), context_for()) == []
    assert validate_reply(reply("The rook on a8 is attacked.", FORK), context_for()) == []
    assert GroundingErrorKind.PIECE_MISMATCH in kinds(
        reply(text.replace("knight on a8", "bishop on a8"), FORK), context_for()
    )


@pytest.mark.parametrize(
    "text",
    [
        "Good catch! After Nc7+ the rook falls.",
        "Let me redo that. Watch the rook on a8.",
        "Sorry about that, here is a better note.",
        "Here is a correction: keep an eye on forks.",
        "I will rewrite my previous reply: develop your pieces.",
    ],
)
def test_replies_that_address_the_correction_are_rejected(text: str) -> None:
    assert GroundingErrorKind.META_COMMENTARY in kinds(reply(text, FORK), context_for())


def test_ordinary_coaching_is_not_meta_commentary() -> None:
    text = "You're right, that is a fork. Keep looking for pieces that can be hit at once."

    assert validate_reply(reply(text), context_for()) == []


class FirstReplyThenRealModel:
    """Our own LanguageModel: a fixed bad first reply, then the real model for the retry."""

    def __init__(self, first: CoachReply, real: AnthropicLanguageModel) -> None:
        self._first: CoachReply | None = first
        self._real = real
        self.retry_text = ""

    async def structured[ParsedModel: BaseModel](
        self,
        *,
        tier: ModelTier,
        system: str,
        messages: Sequence[ChatMessage],
        schema: type[ParsedModel],
    ) -> StructuredReply[ParsedModel]:
        if self._first is not None:
            first, self._first = self._first, None
            if not isinstance(first, schema):
                raise AssertionError("schema mismatch")
            return StructuredReply(first, USAGE)
        result = await self._real.structured(
            tier=tier, system=system, messages=messages, schema=schema
        )
        self.retry_text = str(result.parsed)
        return result

    def stream_text(
        self,
        *,
        tier: ModelTier,
        system: str,
        messages: Sequence[ChatMessage],
        on_usage: Callable[[Usage], None] | None = None,
    ) -> AsyncIterator[str]:
        return self._real.stream_text(
            tier=tier, system=system, messages=messages, on_usage=on_usage
        )


@pytest.mark.llm
async def test_real_regeneration_carries_no_meta_commentary() -> None:
    settings = Settings(_env_file=None, llm_provider="anthropic")
    try:
        api_key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else None
        await anthropic.AsyncAnthropic(api_key=api_key).models.retrieve(settings.cheap_model)
    except (anthropic.AuthenticationError, TypeError):
        pytest.skip("no Anthropic credentials resolve (set ANTHROPIC_API_KEY or `ant auth login`)")
    model = FirstReplyThenRealModel(
        reply("Nc7+ forks the rook on h8 and the king.", FORK), AnthropicLanguageModel(settings)
    )

    result = await coach_reply_with_grounding(model, context_for())

    print(f"REGENERATED ({result.source.value}): {result.text}")
    assert result.source is ReplySource.LLM_REGENERATED
    assert (
        validate_reply(CoachReply(text=result.text, fact_ids=result.fact_ids), context_for()) == []
    )
