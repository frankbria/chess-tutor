import re
from collections.abc import Iterator
from datetime import UTC, datetime

import anthropic
import chess
import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from chess_tutor.analysis.move_delta import move_delta
from chess_tutor.coach.grounding import coach_reply_with_grounding, validate_reply
from chess_tutor.coach.llm import AnthropicLanguageModel
from chess_tutor.coach.profile import (
    CoachProfile,
    ConceptProfile,
    build_coach_profile,
    style_directive,
)
from chess_tutor.coach.prompt import CoachContext, CoachEvent, CoachReply
from chess_tutor.concepts.state import new_state
from chess_tutor.concepts.tagger import tag_mistake
from chess_tutor.concepts.taxonomy import Concept
from chess_tutor.database import create_database_engine, run_migrations, seed_default_player
from chess_tutor.engines.stockfish import StockfishPool
from chess_tutor.facts.packet import build_fact_packet
from chess_tutor.models import ConceptStatus, GameMode
from chess_tutor.openings.phase import GamePhase
from chess_tutor.settings import Settings

PLAYER_ID = 1
HANGING = Concept.HANGING_PIECE
FORK = Concept.MISSED_FORK


@pytest.fixture
def engine(settings: Settings) -> Iterator[Engine]:
    run_migrations(settings)
    database_engine = create_database_engine(settings)
    seed_default_player(database_engine)
    yield database_engine
    database_engine.dispose()


def store_state(session: Session, concept: Concept, status: ConceptStatus, count: int) -> None:
    row = new_state(PLAYER_ID, concept.value)
    row.state, row.count, row.updated_at = status, count, datetime(2026, 11, 23, tzinfo=UTC)
    session.add(row)
    session.commit()


def profile_of(
    engine: Engine, in_play: list[Concept], phrasings: dict[Concept, list[str]] | None = None
) -> CoachProfile:
    with Session(engine) as session:
        return build_coach_profile(session, PLAYER_ID, in_play, phrasings or {})


def test_each_state_gets_its_directive() -> None:
    directives = {status: style_directive(HANGING, status, 4).lower() for status in ConceptStatus}

    assert "explain it fully" in directives[ConceptStatus.NEW]
    assert "socratic" in directives[ConceptStatus.RECURRING]
    assert "4 games" in directives[ConceptStatus.RECURRING]
    assert "current focus" in directives[ConceptStatus.FOCUS]
    assert "hanging piece puzzles" in directives[ConceptStatus.FOCUS]
    assert "brief" in directives[ConceptStatus.IMPROVING]
    assert "this one's back" in directives[ConceptStatus.MASTERED]


def test_a_concept_without_history_is_new(engine: Engine) -> None:
    (entry,) = profile_of(engine, [HANGING]).concepts

    assert (entry.state, entry.count, entry.label) == (
        ConceptStatus.NEW,
        0,
        "Leaving pieces undefended",
    )


def test_stored_state_and_count_are_used(engine: Engine) -> None:
    with Session(engine) as session:
        store_state(session, HANGING, ConceptStatus.RECURRING, 3)

    (entry,) = profile_of(engine, [HANGING]).concepts

    assert (entry.state, entry.count) == (ConceptStatus.RECURRING, 3)
    assert "3 games" in entry.style


def test_focus_concepts_are_included_even_when_not_in_play(engine: Engine) -> None:
    with Session(engine) as session:
        store_state(session, FORK, ConceptStatus.FOCUS, 5)

    concepts = [entry.concept for entry in profile_of(engine, [HANGING]).concepts]

    assert concepts == [HANGING, FORK]


def test_a_mastered_concept_is_mentioned_only_when_it_recurs(engine: Engine) -> None:
    with Session(engine) as session:
        store_state(session, FORK, ConceptStatus.MASTERED, 6)

    assert profile_of(engine, [HANGING]).concepts[0].concept is HANGING
    assert [entry.concept for entry in profile_of(engine, [HANGING]).concepts] == [HANGING]
    (back,) = profile_of(engine, [FORK]).concepts
    assert back.state is ConceptStatus.MASTERED
    assert "this one's back" in back.style


def test_only_the_last_three_phrasings_per_concept_are_passed(engine: Engine) -> None:
    phrasings = {HANGING: ["a", "b", "c", "d"], FORK: ["other"]}

    (entry,) = profile_of(engine, [HANGING], phrasings).concepts

    assert entry.recent_phrasings == ["b", "c", "d"]


GROUNDING_FEN = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"
HANGING_MOVE = "Nxe5"


def settings_with_credentials() -> Settings:
    return Settings(_env_file=None, llm_provider="anthropic")


@pytest.mark.llm
async def test_the_same_mistake_coached_three_times_escalates() -> None:
    settings = settings_with_credentials()
    try:
        api_key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else None
        await anthropic.AsyncAnthropic(api_key=api_key).models.retrieve(settings.cheap_model)
    except (anthropic.AuthenticationError, TypeError):
        pytest.skip("no Anthropic credentials resolve (set ANTHROPIC_API_KEY or `ant auth login`)")
    model = AnthropicLanguageModel(settings)
    board = chess.Board(GROUNDING_FEN)
    stages = [(ConceptStatus.NEW, 0), (ConceptStatus.RECURRING, 3), (ConceptStatus.FOCUS, 4)]
    texts: list[str] = []
    async with StockfishPool(settings.stockfish_path, 1) as pool:
        delta = await move_delta(board, board.parse_san(HANGING_MOVE), pool)
    assert Concept.HANGING_PIECE in tag_mistake(delta, GamePhase.MIDDLEGAME, board)
    for status, count in stages:
        profile = CoachProfile(
            concepts=[
                ConceptProfile(
                    concept=HANGING,
                    label="Leaving pieces undefended",
                    state=status,
                    count=count,
                    style=style_directive(HANGING, status, count),
                    recent_phrasings=texts[-3:],
                )
            ]
        )
        context = CoachContext(
            mode=GameMode.PASSIVE,
            active_pre_lock_in=False,
            phase=GamePhase.MIDDLEGAME,
            event=CoachEvent.COMMENT_ON_MOVE,
            move_san=HANGING_MOVE,
            fact_packet=build_fact_packet(board),
            move_delta_facts=delta.facts,
            best_move_san=delta.best_move_san,
            best_line=delta.best_line,
            profile=profile,
        )
        grounded = await coach_reply_with_grounding(model, context)
        assert not validate_reply(
            CoachReply(text=grounded.text, fact_ids=grounded.fact_ids), context
        )
        assert not {"fact", "facts", "context"} & set(re.findall(r"[a-z]+", grounded.text.lower()))
        texts.append(grounded.text)
        print(f"[{status.value} x{count}] {grounded.source.value}: {grounded.text}")
    words = [len(text.split()) for text in texts]
    assert len(set(texts)) == 3
    assert words[1] < words[0] or "?" in texts[1]
    assert words[2] < words[0] or "focus" in texts[2].lower()
