from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from scripted_opponent import ScriptedOpponent
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from chess_tutor.concepts.state import (
    new_state,
    refresh_concept_states,
    update_on_clean_game,
    update_on_occurrence,
)
from chess_tutor.concepts.tagger import record_concepts
from chess_tutor.concepts.taxonomy import Concept
from chess_tutor.database import create_database_engine, run_migrations, seed_default_player
from chess_tutor.games.service import ColorChoice, GameService
from chess_tutor.main import create_app
from chess_tutor.models import ConceptState, ConceptStatus, GameMode
from chess_tutor.settings import ConceptStateThresholds, Settings

PLAYER_ID = 1
NOW = datetime(2026, 11, 23, tzinfo=UTC)
THRESHOLDS = ConceptStateThresholds()


def run_games(row: ConceptState, pattern: str, first_game_id: int = 1) -> ConceptState:
    """`x` is a game with the concept, `.` a clean game."""
    for offset, kind in enumerate(pattern):
        step = update_on_occurrence if kind == "x" else update_on_clean_game
        step(row, first_game_id + offset, NOW, THRESHOLDS)
    return row


def state_after(pattern: str) -> ConceptStatus:
    return run_games(new_state(PLAYER_ID, "pin_blindness"), pattern).state


def test_one_occurrence_is_new_and_stays_new_through_clean_games() -> None:
    assert state_after("x") is ConceptStatus.NEW
    assert state_after("x" + "." * 20) is ConceptStatus.NEW


def test_a_second_nearby_occurrence_makes_it_recurring() -> None:
    assert state_after("xx") is ConceptStatus.RECURRING
    assert state_after("x..x") is ConceptStatus.RECURRING


def test_two_distant_occurrences_are_not_recurring() -> None:
    assert state_after("x" + "." * 8 + "x") is ConceptStatus.NEW


def test_four_close_occurrences_make_it_focus_but_four_spread_out_do_not() -> None:
    assert state_after("xxxx") is ConceptStatus.FOCUS
    assert state_after("xx.x.x") is ConceptStatus.RECURRING


def test_clean_games_lead_to_improving_then_mastered() -> None:
    assert state_after("xxxx..") is ConceptStatus.FOCUS
    assert state_after("xxxx...") is ConceptStatus.IMPROVING
    assert state_after("xxxx" + "." * 9) is ConceptStatus.IMPROVING
    assert state_after("xxxx" + "." * 10) is ConceptStatus.MASTERED
    assert state_after("xx...") is ConceptStatus.IMPROVING


def test_an_occurrence_resets_the_clean_streak() -> None:
    row = run_games(new_state(PLAYER_ID, "pin_blindness"), "xx..x..")
    assert row.state is ConceptStatus.RECURRING and row.games_since_last_seen == 2


def test_recurrence_after_improving_or_mastered_demotes() -> None:
    assert state_after("xx...x") is ConceptStatus.RECURRING
    assert state_after("xxxx" + "." * 10 + "x") is ConceptStatus.RECURRING


def test_recurrence_after_mastered_goes_back_to_focus_when_frequent_again() -> None:
    row = run_games(new_state(PLAYER_ID, "pin_blindness"), "xxxx" + "." * 10)
    assert row.state is ConceptStatus.MASTERED
    run_games(row, "xxx", first_game_id=15)
    assert ConceptStatus(row.state) is ConceptStatus.FOCUS


def test_the_recency_score_decays_over_clean_games_and_jumps_on_occurrence() -> None:
    row = run_games(new_state(PLAYER_ID, "pin_blindness"), "xx")
    score = row.recency_score
    assert score == pytest.approx(1.8)
    run_games(row, "...", first_game_id=3)
    assert row.recency_score == pytest.approx(score * 0.8**3)
    run_games(row, "x", first_game_id=6)
    assert row.recency_score == pytest.approx(score * 0.8**3 * 0.8 + 1)
    assert (row.count, row.last_seen_game_id) == (3, 6)


@pytest.fixture
def engine(settings: Settings) -> Iterator[Engine]:
    run_migrations(settings)
    database_engine = create_database_engine(settings)
    seed_default_player(database_engine)
    yield database_engine
    database_engine.dispose()


async def finish_game_with(engine: Engine, concepts: list[Concept]) -> int:
    service = GameService(engine, ScriptedOpponent())
    game = await service.create_game(PLAYER_ID, 1200, ColorChoice.WHITE, GameMode.PASSIVE)
    with Session(engine) as session:
        record_concepts(session, PLAYER_ID, game.id, 1, concepts)
        record_concepts(session, PLAYER_ID, game.id, 3, concepts[:1])  # repeats count once
        session.commit()
    service.resign(game.id)
    return game.id


def read_states(engine: Engine) -> dict[str, ConceptState]:
    with Session(engine) as session:
        return {row.concept: row for row in session.query(ConceptState)}


async def test_finishing_games_advances_the_states(engine: Engine) -> None:
    for _ in range(2):
        await finish_game_with(engine, [Concept.PIN_BLINDNESS, Concept.EARLY_QUEEN])
    last_game = await finish_game_with(engine, [Concept.PIN_BLINDNESS])

    states = read_states(engine)
    pin = states["pin_blindness"]
    assert (pin.state, pin.count, pin.last_seen_game_id) == (ConceptStatus.RECURRING, 3, last_game)
    queen = states["early_queen"]
    assert (queen.state, queen.count, queen.games_since_last_seen) == (
        ConceptStatus.RECURRING,
        2,
        1,
    )


async def test_refreshing_a_game_twice_does_not_double_apply(engine: Engine) -> None:
    game_id = await finish_game_with(engine, [Concept.PIN_BLINDNESS])
    with Session(engine) as session:
        refresh_concept_states(session, PLAYER_ID, game_id, NOW, THRESHOLDS)
        session.commit()

    pin = read_states(engine)["pin_blindness"]
    assert (pin.count, pin.recency_score) == (1, 1.0)


def test_concepts_endpoint_orders_by_state_and_describes_each(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        engine = client.app.state.engine  # type: ignore[attr-defined]
        with Session(engine) as session:
            for concept, pattern in (
                ("pin_blindness", "x"),
                ("early_queen", "xxxx"),
                ("missed_fork", "xxxx..."),
                ("back_rank", "xx"),
                ("missed_mate", "xxxx" + "." * 10),
            ):
                row = run_games(new_state(PLAYER_ID, concept), pattern)
                row.last_seen_game_id = None  # no such game rows in this test
                session.add(row)
            session.commit()

        body = client.get("/player/concepts").json()

    assert [(item["concept"], item["state"]) for item in body] == [
        ("early_queen", "focus"),
        ("back_rank", "recurring"),
        ("missed_fork", "improving"),
        ("pin_blindness", "new"),
        ("missed_mate", "mastered"),
    ]
    assert body[0]["label"] == "Bringing the queen out too early"
    assert body[0]["practice_themes"] and body[0]["count"] == 4 and body[0]["recency_score"] > 2.5
