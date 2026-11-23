import asyncio
from collections.abc import Iterator

import chess
import pytest
from fastapi.testclient import TestClient
from scripted_opponent import ScriptedOpponent
from sqlalchemy import Engine

from chess_tutor.database import create_database_engine, run_migrations, seed_default_player
from chess_tutor.games.service import ColorChoice, GameService, InvalidGameSettingsError
from chess_tutor.main import create_app
from chess_tutor.models import GameMode, GameResult, GameStatus, PlayerColor, TimeControl
from chess_tutor.settings import Settings

PLAYER_ID = 1
MINUTE = 60_000


class FakeClock:
    def __init__(self) -> None:
        self.now_ms = 5_000_000_000

    def __call__(self) -> int:
        return self.now_ms

    def advance(self, milliseconds: int) -> None:
        self.now_ms += milliseconds


@pytest.fixture
def engine(settings: Settings) -> Iterator[Engine]:
    run_migrations(settings)
    database_engine = create_database_engine(settings)
    seed_default_player(database_engine)
    yield database_engine
    database_engine.dispose()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def timed_service(engine: Engine, clock: FakeClock, *replies: str) -> GameService:
    return GameService(engine, ScriptedOpponent(*replies), clock=clock)


def clock_of(service: GameService, game_id: int) -> tuple[int, int]:
    game = service.get_game(game_id)
    state = service.clock_state(game, service.get_board(game_id).turn)
    assert state is not None
    return state.white_ms, state.black_ms


def test_presets_are_base_minutes_plus_increment_seconds() -> None:
    assert (TimeControl.RAPID_10_5.base_ms, TimeControl.RAPID_10_5.increment_ms) == (600_000, 5000)
    assert (TimeControl.RAPID_15_10.base_ms, TimeControl.RAPID_15_10.increment_ms) == (
        900_000,
        10_000,
    )


async def test_move_charges_the_mover_and_adds_the_increment(
    engine: Engine, clock: FakeClock
) -> None:
    service = timed_service(engine, clock, "e5")
    game = await service.create_game(
        PLAYER_ID, 1000, ColorChoice.WHITE, time_control=TimeControl.RAPID_10_5
    )
    assert clock_of(service, game.id) == (10 * MINUTE, 10 * MINUTE)

    clock.advance(7000)
    await service.play_move(game.id, "e2e4")  # opponent replies in the same fake instant

    assert clock_of(service, game.id) == (10 * MINUTE - 7000 + 5000, 10 * MINUTE + 5000)


async def test_the_running_clock_counts_down_on_read(engine: Engine, clock: FakeClock) -> None:
    service = timed_service(engine, clock)
    game = await service.create_game(
        PLAYER_ID, 1000, ColorChoice.WHITE, time_control=TimeControl.BLITZ_5_3
    )

    clock.advance(12_000)

    state = service.clock_state(service.get_game(game.id), chess.WHITE)
    assert state is not None
    assert (state.white_ms, state.black_ms, state.running) == (
        5 * MINUTE - 12_000,
        5 * MINUTE,
        PlayerColor.WHITE,
    )


async def test_opponent_think_time_counts_against_the_opponent(
    engine: Engine, clock: FakeClock
) -> None:
    class SlowOpponent(ScriptedOpponent):
        async def choose_move(self, board: chess.Board, elo: int) -> chess.Move:
            clock.advance(4000)
            return await super().choose_move(board, elo)

    service = GameService(engine, SlowOpponent("e5"), clock=clock)
    game = await service.create_game(
        PLAYER_ID, 1000, ColorChoice.WHITE, time_control=TimeControl.RAPID_10_5
    )

    await service.play_move(game.id, "e2e4")

    assert clock_of(service, game.id) == (10 * MINUTE + 5000, 10 * MINUTE - 4000 + 5000)


async def test_player_flagging_on_a_move_loses_on_time(engine: Engine, clock: FakeClock) -> None:
    service = timed_service(engine, clock, "e5")
    game = await service.create_game(
        PLAYER_ID, 1000, ColorChoice.WHITE, time_control=TimeControl.BLITZ_5_3
    )

    clock.advance(5 * MINUTE)
    finished = await service.play_move(game.id, "e2e4")

    assert (finished.status, finished.result) == (GameStatus.FINISHED, GameResult.BLACK_WINS)
    assert finished.ended_on_time
    assert service.get_board(game.id).move_stack == []
    assert clock_of(service, game.id) == (0, 5 * MINUTE)


async def test_opponent_flagging_during_its_reply_wins_for_the_player(
    engine: Engine, clock: FakeClock
) -> None:
    class StuckOpponent(ScriptedOpponent):
        async def choose_move(self, board: chess.Board, elo: int) -> chess.Move:
            clock.advance(5 * MINUTE)
            return await super().choose_move(board, elo)

    service = GameService(engine, StuckOpponent("e5"), clock=clock)
    game = await service.create_game(
        PLAYER_ID, 1000, ColorChoice.WHITE, time_control=TimeControl.BLITZ_5_3
    )

    finished = await service.play_move(game.id, "e2e4")

    assert (finished.status, finished.result) == (GameStatus.FINISHED, GameResult.WHITE_WINS)
    assert finished.ended_on_time
    assert len(service.get_board(game.id).move_stack) == 1


async def test_check_flag_ends_a_game_whose_side_to_move_ran_out(
    engine: Engine, clock: FakeClock
) -> None:
    service = timed_service(engine, clock)
    game = await service.create_game(
        PLAYER_ID, 1000, ColorChoice.WHITE, time_control=TimeControl.BLITZ_5_3
    )
    assert (await service.check_flag(game.id)).status is GameStatus.ACTIVE

    clock.advance(5 * MINUTE + 1)
    flagged = await service.check_flag(game.id)

    assert (flagged.status, flagged.result, flagged.ended_on_time) == (
        GameStatus.FINISHED,
        GameResult.BLACK_WINS,
        True,
    )
    assert service.get_game(game.id).pgn is not None


async def test_untimed_games_have_no_clock(engine: Engine, clock: FakeClock) -> None:
    service = timed_service(engine, clock)
    game = await service.create_game(PLAYER_ID, 1000, ColorChoice.WHITE)

    clock.advance(100 * MINUTE)

    assert (await service.check_flag(game.id)).status is GameStatus.ACTIVE
    assert service.clock_state(game, chess.WHITE) is None


@pytest.mark.parametrize("mode", [GameMode.PASSIVE, GameMode.ACTIVE])
async def test_tutor_modes_reject_a_time_control(
    engine: Engine, clock: FakeClock, mode: GameMode
) -> None:
    service = timed_service(engine, clock)

    with pytest.raises(InvalidGameSettingsError):
        await service.create_game(PLAYER_ID, 1000, ColorChoice.WHITE, mode, TimeControl.RAPID_10_5)


def test_api_rejects_a_time_control_in_tutor_modes_and_unknown_presets(settings: Settings) -> None:
    with TestClient(create_app(settings, ScriptedOpponent())) as api:
        body = {"opponent_elo": 1000, "color": "white", "time_control": "10+5"}
        assert api.post("/games", json={**body, "mode": "passive"}).status_code == 422
        assert api.post("/games", json={**body, "time_control": "1+0"}).status_code == 422


def test_clock_survives_a_reload(settings: Settings) -> None:
    app = create_app(settings, ScriptedOpponent("e5"))
    with TestClient(app) as api:
        created = api.post(
            "/games",
            json={
                "opponent_elo": 1000,
                "color": "white",
                "time_control": "10+5",
                "mode": "regular",
            },
        ).json()
        assert created["time_control"] == "10+5"
        api.post(f"/games/{created['id']}/moves", json={"uci": "e2e4"})

    with TestClient(create_app(settings, ScriptedOpponent())) as reloaded:
        game = reloaded.get(f"/games/{created['id']}").json()

    assert game["clock"]["running"] == "white"
    assert 10 * MINUTE < game["clock"]["white_ms"] <= 10 * MINUTE + 5000
    assert 10 * MINUTE < game["clock"]["black_ms"] <= 10 * MINUTE + 5000


def test_get_flags_a_game_that_ran_out_while_nobody_watched(settings: Settings) -> None:
    app = create_app(settings, ScriptedOpponent())
    with TestClient(app) as api:
        created = api.post(
            "/games", json={"opponent_elo": 1000, "color": "white", "time_control": "5+3"}
        ).json()
        service: GameService = app.state.game_service
        started = service._clock()
        service._clock = lambda: started + 6 * MINUTE

        game = api.get(f"/games/{created['id']}").json()

    assert (game["status"], game["result"], game["ended_on_time"]) == ("finished", "0-1", True)
    assert game["clock"]["running"] is None


async def test_a_flag_check_racing_a_move_leaves_one_consistent_outcome(
    engine: Engine, clock: FakeClock
) -> None:
    service = timed_service(engine, clock, "e5")
    game = await service.create_game(
        PLAYER_ID, 1000, ColorChoice.WHITE, time_control=TimeControl.BLITZ_5_3
    )
    clock.advance(5 * MINUTE)

    await asyncio.gather(
        service.check_flag(game.id), service.play_move(game.id, "e2e4"), return_exceptions=True
    )

    finished = service.get_game(game.id)
    assert (finished.status, finished.result, finished.ended_on_time) == (
        GameStatus.FINISHED,
        GameResult.BLACK_WINS,
        True,
    )
    assert service.get_board(game.id).move_stack == []
