from collections.abc import Iterator

import pytest
from scripted_opponent import ScriptedOpponent
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from chess_tutor.database import create_database_engine, run_migrations, seed_default_player
from chess_tutor.games.service import (
    ColorChoice,
    GameNotActiveError,
    GameService,
    NothingToTakeBackError,
    OpponentUnavailableError,
    TakebackNotAllowedError,
)
from chess_tutor.models import Game, GameMode, Move, RatingHistory
from chess_tutor.rating.history import takeback_multiplier
from chess_tutor.settings import RatingWeights, Settings

PLAYER_ID = 1


@pytest.fixture
def engine(settings: Settings) -> Iterator[Engine]:
    run_migrations(settings)
    database_engine = create_database_engine(settings)
    seed_default_player(database_engine)
    yield database_engine
    database_engine.dispose()


def played_ucis(engine: Engine, game_id: int) -> list[str]:
    with Session(engine) as session:
        return list(
            session.scalars(select(Move.uci).where(Move.game_id == game_id).order_by(Move.ply))
        )


async def passive_game(
    engine: Engine, *replies: str, color: ColorChoice = ColorChoice.WHITE
) -> tuple[GameService, Game]:
    service = GameService(engine, ScriptedOpponent(*replies))
    return service, await service.create_game(PLAYER_ID, 1200, color, GameMode.PASSIVE)


async def test_takeback_undoes_the_players_move_and_the_reply_and_is_counted(
    engine: Engine,
) -> None:
    service, game = await passive_game(engine, "e5", "Nc6", "d5")
    await service.play_move(game.id, "e2e4")
    await service.play_move(game.id, "g1f3")

    taken_back = await service.takeback(game.id)

    assert played_ucis(engine, game.id) == ["e2e4", "e7e5"]
    assert taken_back.takeback_count == 1
    assert (await service.play_move(game.id, "d2d4")).takeback_count == 1  # the ply is free again
    assert played_ucis(engine, game.id)[:3] == ["e2e4", "e7e5", "d2d4"]


async def test_takeback_before_the_opponent_replied_undoes_only_the_players_move(
    engine: Engine,
) -> None:
    service, game = await passive_game(engine)  # the scripted opponent has no reply to give
    with pytest.raises(OpponentUnavailableError):
        await service.play_move(game.id, "e2e4")

    await service.takeback(game.id)

    assert played_ucis(engine, game.id) == []


async def test_takeback_as_black_keeps_the_opponents_opening_move(engine: Engine) -> None:
    service, game = await passive_game(engine, "e4", "Nf3", color=ColorChoice.BLACK)
    await service.play_move(game.id, "e7e5")

    await service.takeback(game.id)

    assert played_ucis(engine, game.id) == ["e2e4"]


async def test_takeback_resets_the_opening_to_what_is_still_played(engine: Engine) -> None:
    service, game = await passive_game(engine, "e5", "Nc6", "Nf6")
    for uci in ("e2e4", "g1f3", "f1b5"):
        await service.play_move(game.id, uci)
    with Session(engine) as session:
        assert session.get_one(Game, game.id).opening_name is not None

    await service.takeback(game.id)
    await service.takeback(game.id)
    await service.takeback(game.id)

    with Session(engine) as session:
        stored = session.get_one(Game, game.id)
        assert (stored.opening_eco, stored.opening_name, stored.takeback_count) == (None, None, 3)


async def test_nothing_to_take_back_before_the_players_first_move(engine: Engine) -> None:
    service, game = await passive_game(engine, "e4", color=ColorChoice.BLACK)

    with pytest.raises(NothingToTakeBackError):
        await service.takeback(game.id)

    assert played_ucis(engine, game.id) == ["e2e4"]


@pytest.mark.parametrize("mode", [GameMode.REGULAR, GameMode.ACTIVE])
async def test_takeback_is_refused_outside_passive_mode(engine: Engine, mode: GameMode) -> None:
    service = GameService(engine, ScriptedOpponent("e5"))
    game = await service.create_game(PLAYER_ID, 1200, ColorChoice.WHITE, mode)
    if mode is GameMode.ACTIVE:  # Active moves are proposed, then locked in
        await service.propose_move(game.id, "e2e4")
        await service.lock_in(game.id)
    else:
        await service.play_move(game.id, "e2e4")

    with pytest.raises(TakebackNotAllowedError):
        await service.takeback(game.id)

    assert played_ucis(engine, game.id) == ["e2e4", "e7e5"]
    assert service.get_game(game.id).takeback_count == 0


async def test_takeback_is_refused_once_the_game_is_over(engine: Engine) -> None:
    service, game = await passive_game(engine, "e5")
    await service.play_move(game.id, "e2e4")
    service.resign(game.id)

    with pytest.raises(GameNotActiveError):
        await service.takeback(game.id)


async def test_takebacks_lower_the_weight_the_game_is_rated_with(engine: Engine) -> None:
    service, game = await passive_game(engine, "e5", "e5", "e5")
    for _ in range(3):
        await service.play_move(game.id, "e2e4")
        await service.takeback(game.id)
    service.resign(game.id)

    with Session(engine) as session:
        weight = session.scalars(select(RatingHistory.weight)).one()
    assert weight == pytest.approx(0.75 * 0.7)


def test_takeback_weight_multiplier_steps_down_to_a_floor() -> None:
    weights = RatingWeights()

    assert [takeback_multiplier(weights, count) for count in (0, 2, 5, 6, 40)] == [
        pytest.approx(value) for value in (1.0, 0.8, 0.5, 0.5, 0.5)
    ]
