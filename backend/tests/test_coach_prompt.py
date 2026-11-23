import json
from pathlib import Path

import anthropic
import chess
import pytest
from pydantic import ValidationError
from scripted_language_model import ScriptedLanguageModel

from chess_tutor.analysis.move_delta import move_delta
from chess_tutor.coach.llm import AnthropicLanguageModel, ModelTier
from chess_tutor.coach.prompt import (
    SYSTEM_PROMPT,
    CoachContext,
    CoachEvent,
    CoachReply,
    build_coach_request,
    generate_coach_reply,
)
from chess_tutor.engines.stockfish import StockfishPool
from chess_tutor.facts.models import FactPacket
from chess_tutor.facts.packet import build_fact_packet
from chess_tutor.models import GameMode
from chess_tutor.openings.phase import GamePhase
from chess_tutor.settings import Settings

SNAPSHOT = Path(__file__).parent / "snapshots" / "coach_request_active_hint.json"
FORK_ON_C7 = "r3k3/8/8/3N4/8/8/8/4K3 w - - 0 1"
KNIGHT_FORK_AVAILABLE = "3qk2r/pp3ppp/8/8/1n6/8/PP3PPP/R2QK1N1 w - - 0 1"
WORD_LIMIT = 60


def fork_context(**overrides: object) -> CoachContext:
    values: dict[str, object] = {
        "mode": GameMode.ACTIVE,
        "phase": GamePhase.MIDDLEGAME,
        "event": CoachEvent.HINT,
        "move_san": "Kf2",
        "fact_packet": build_fact_packet(chess.Board(FORK_ON_C7)),
        "best_move_san": "Nc7+",
        "best_line": ["Nc7+", "Kd7", "Nxa8"],
        "hint_level": 2,
        "active_pre_lock_in": True,
        "profile_summary": "Often misses knight forks.",
        "recent_coach_messages": ["Watch the knight."],
    }
    return CoachContext.model_validate(values | overrides)


def assembled_request(context: CoachContext) -> dict[str, object]:
    system, messages = build_coach_request(context)
    return {
        "system": system,
        "messages": [
            {"role": message.role, "content": json.loads(message.content)} for message in messages
        ],
    }


def everything_sent(context: CoachContext) -> str:
    system, messages = build_coach_request(context)
    return system + "".join(message.content for message in messages)


def test_pre_lock_in_request_never_contains_the_best_move() -> None:
    context = fork_context()
    packet_has_it = any(
        line[:1] == ["Nc7+"] for fact in context.fact_packet.facts for line in fact.san_lines
    )

    sent = everything_sent(context)

    assert packet_has_it  # the fixture really would leak without the filter
    assert "Nc7" not in sent
    assert "Nxa8" not in sent
    assert '"best_move"' not in sent


def test_after_lock_in_the_best_move_and_its_facts_are_sent() -> None:
    sent = everything_sent(fork_context(active_pre_lock_in=False))

    assert "Nc7+" in sent
    assert '"best_line":["Nc7+","Kd7","Nxa8"]' in sent


def test_a_fact_summary_naming_the_best_move_is_dropped_too() -> None:
    packet = build_fact_packet(chess.Board(FORK_ON_C7))
    leaky = packet.facts[0].model_copy(
        update={"id": "x:1", "summary": "Nc7+ wins material", "san_lines": []}
    )
    context = fork_context(
        fact_packet=FactPacket(fen=packet.fen, side_to_move=packet.side_to_move, facts=[leaky])
    )

    assert "x:1" not in everything_sent(context)


def leaky_fact_context(**fact_fields: object) -> CoachContext:
    packet = build_fact_packet(chess.Board(FORK_ON_C7))
    fact = packet.facts[0].model_copy(update={"id": "leaky:1", "san_lines": []} | fact_fields)
    return fork_context(
        fact_packet=FactPacket(fen=packet.fen, side_to_move=packet.side_to_move, facts=[fact])
    )


@pytest.mark.parametrize(
    "fact_fields",
    [
        {"san_lines": [["Kd7", "Nc7"]]},  # not the first move of the line
        {"san_lines": [["Kd7"], ["Ke7", "Nc7#"]]},  # another line, other suffix
        {"summary": "Nc7—it forks the king and rook"},
        {"summary": "the fork (Nc7+) wins a rook"},
        {"summary": "play Nc7, then"},
    ],
)
def test_facts_naming_the_best_move_anywhere_are_dropped(fact_fields: dict[str, object]) -> None:
    assert "leaky:1" not in everything_sent(leaky_fact_context(**fact_fields))


def test_a_similar_looking_summary_is_kept() -> None:
    sent = everything_sent(leaky_fact_context(summary="the Nc70 square, Nc7x and ANc7 are not moves"))

    assert "leaky:1" in sent


def test_questions_pre_lock_in_carry_the_hint_level() -> None:
    context = fork_context(event=CoachEvent.ANSWER_QUESTION, player_message="why is that bad?")
    _, messages = build_coach_request(context)
    payload = json.loads(messages[0].content)

    assert payload["hint_level"] == 2
    assert payload["locked_in"] is False
    assert "best_move" not in payload
    assert "follow the hint level rules" in SYSTEM_PROMPT


def test_the_lock_in_flag_is_required() -> None:
    with pytest.raises(ValidationError):
        CoachContext.model_validate(
            fork_context().model_dump(mode="json", exclude={"active_pre_lock_in"})
        )


def test_system_prompt_is_identical_for_every_context() -> None:
    systems = {
        build_coach_request(context)[0]
        for context in (
            fork_context(),
            fork_context(mode=GameMode.PASSIVE, event=CoachEvent.ASK_THOUGHT, hint_level=None),
            fork_context(player_message="why?", phase=GamePhase.ENDGAME),
        )
    }

    assert systems == {SYSTEM_PROMPT}


def test_request_matches_the_checked_in_snapshot() -> None:
    assert assembled_request(fork_context()) == json.loads(SNAPSHOT.read_text())


def test_only_the_last_few_recent_messages_are_sent() -> None:
    context = fork_context(recent_coach_messages=[f"message {n}" for n in range(8)])

    sent = everything_sent(context)

    assert "message 7" in sent
    assert "message 2" not in sent


async def test_scripted_model_round_trip() -> None:
    model = ScriptedLanguageModel()
    model.queue_structured(CoachReply(text="What can the knight reach?", fact_ids=["a"]))
    context = fork_context()

    reply = await generate_coach_reply(model, context)

    assert reply.parsed.fact_ids == ["a"]
    call = model.calls[0]
    assert call.tier is ModelTier.COACHING
    assert (call.system, list(call.messages)) == build_coach_request(context)


@pytest.mark.llm
async def test_real_coach_cites_only_known_facts_and_hides_the_best_move() -> None:
    settings = Settings(_env_file=None, llm_provider="anthropic")
    try:
        api_key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else None
        await anthropic.AsyncAnthropic(api_key=api_key).models.retrieve(settings.cheap_model)
    except (anthropic.AuthenticationError, TypeError):
        pytest.skip("no Anthropic credentials resolve (set ANTHROPIC_API_KEY or `ant auth login`)")
    board = chess.Board(KNIGHT_FORK_AVAILABLE)
    async with StockfishPool(settings.stockfish_path, 1) as pool:
        delta = await move_delta(board, chess.Move.from_uci("d1g4"), pool)
    assert delta.best_move_san is not None
    context = CoachContext(
        mode=GameMode.ACTIVE,
        phase=GamePhase.MIDDLEGAME,
        event=CoachEvent.HINT,
        move_san=delta.move_san,
        fact_packet=build_fact_packet(board),
        move_delta_facts=delta.facts,
        best_move_san=delta.best_move_san,
        best_line=delta.best_line,
        hint_level=3,
        active_pre_lock_in=True,
    )

    reply = await generate_coach_reply(AnthropicLanguageModel(settings), context)

    print("COACH TEXT:", reply.parsed.text, reply.parsed.fact_ids)
    known = {fact.id for fact in [*context.fact_packet.facts, *context.move_delta_facts]}
    assert reply.parsed.text.strip()
    assert set(reply.parsed.fact_ids) <= known
    assert len(reply.parsed.text.split()) <= WORD_LIMIT
    assert delta.best_move_san not in reply.parsed.text
