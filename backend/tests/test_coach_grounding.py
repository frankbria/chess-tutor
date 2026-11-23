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
    assert GroundingErrorKind.UNCITED_SQUARE in kinds(reply("Watch the rook on a8."), context_for())


def test_a_square_from_a_fact_that_was_not_cited_is_rejected() -> None:
    text = "Your king on e1 is a long way from the centre."

    assert GroundingErrorKind.UNCITED_SQUARE in kinds(reply(text, FORK), context_for())
    assert validate_reply(reply(text, "activity:white:passive_king"), context_for()) == []


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


def test_the_best_move_is_rejected_before_lock_in_even_in_a_visible_fact() -> None:
    deeper = Fact(
        id="deep", type=FactType.ALLOWS, side=Side.BLACK, summary="x", san_lines=[["Kd7", "Nc7+"]]
    )
    context = with_facts(deeper, active_pre_lock_in=True)

    assert kinds(reply("Kd7, then Nc7+ is the move.", "deep"), context) == {
        GroundingErrorKind.BEST_MOVE_LEAK
    }
    assert GroundingErrorKind.UNKNOWN_FACT in kinds(reply("Hmm.", FORK), context)
    assert validate_reply(reply("Nc7+ is the move.", "deep"), context)  # still leaks without a mark
    assert (
        validate_reply(
            reply("Nc7+ is the move.", "deep"),
            context.model_copy(update={"active_pre_lock_in": False}),
        )
        == []
    )


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
    assert [message.role for message in second.messages] == ["user", "assistant", "user"]
    assert "h8, which is not in any cited fact" in second.messages[-1].content
    assert BAD.text in second.messages[1].content


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


KNIGHT_FORK_ALLOWED = ("r3k3/8/8/3N4/8/8/8/4K3 b - - 0 1", "Kd7")
IGNORED_THREAT = ("rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2", "a6")
QUIET_GOOD_MOVE = ("rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2", "Nf3")
REAL_RUNS_PER_POSITION = 2


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
        for fen, san in (KNIGHT_FORK_ALLOWED, IGNORED_THREAT, QUIET_GOOD_MOVE):
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
            )
            for _ in range(REAL_RUNS_PER_POSITION):
                grounded = await coach_reply_with_grounding(model, context)
                results.append(grounded)
                print(f"[{san}] {grounded.source.value}: {grounded.text} {grounded.fact_ids}")
                assert not validate_reply(
                    CoachReply(text=grounded.text, fact_ids=grounded.fact_ids), context
                )
    first_attempt = sum(result.source is ReplySource.LLM for result in results)
    print(f"FIRST ATTEMPT PASSED: {first_attempt}/{len(results)}")
