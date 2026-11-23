import asyncio
import io
from collections.abc import Iterator

import chess
import chess.pgn
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from chess_tutor.database import create_database_engine, run_migrations, seed_default_player
from chess_tutor.games.service import (
    ColorChoice,
    GameNotActiveError,
    GameNotFoundError,
    GameService,
    IllegalMoveError,
    InvalidGameSettingsError,
    NotPlayersTurnError,
    OpponentUnavailableError,
    board_result,
)
from chess_tutor.main import create_app
from chess_tutor.models import Game, GameMode, GameResult, GameStatus, Move, PlayerColor
from chess_tutor.settings import Settings

PLAYER_ID = 1


class ScriptedOpponent:
    """Test double for our own OpponentEngine protocol: plays a fixed list of SAN moves."""

    def __init__(self, *san_moves: str) -> None:
        self._remaining = list(san_moves)
        self.elos: list[int] = []

    async def start(self) -> None: ...

    async def close(self) -> None: ...

    async def choose_move(self, board: chess.Board, elo: int) -> chess.Move:
        self.elos.append(elo)
        await asyncio.sleep(0)  # a real engine yields while it thinks
        return board.parse_san(self._remaining.pop(0))


@pytest.fixture
def engine(settings: Settings) -> Iterator[Engine]:
    run_migrations(settings)
    database_engine = create_database_engine(settings)
    seed_default_player(database_engine)
    yield database_engine
    database_engine.dispose()


def service_with(engine: Engine, *opponent_moves: str) -> GameService:
    return GameService(engine, ScriptedOpponent(*opponent_moves))


async def play_san(service: GameService, game_id: int, san: str) -> Game:
    return await service.play_move(game_id, service.get_board(game_id).parse_san(san).uci())


def move_count(engine: Engine, game_id: int) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).where(Move.game_id == game_id)) or 0


async def test_create_game_as_white_waits_for_the_player(engine: Engine) -> None:
    service = service_with(engine)

    game = await service.create_game(PLAYER_ID, 1100, ColorChoice.WHITE, GameMode.PASSIVE)

    assert (game.mode, game.player_color, game.status) == (
        GameMode.PASSIVE,
        PlayerColor.WHITE,
        GameStatus.ACTIVE,
    )
    assert (game.opponent_engine, game.opponent_elo, game.result) == ("maia3", 1100, None)
    assert move_count(engine, game.id) == 0


async def test_player_as_black_gets_the_opponents_first_move(engine: Engine) -> None:
    opponent = ScriptedOpponent("e4")
    service = GameService(engine, opponent)

    game = await service.create_game(PLAYER_ID, 900, ColorChoice.BLACK)

    assert service.get_board(game.id).fen().startswith("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP")
    assert opponent.elos == [900]


async def test_random_color_picks_either_side(engine: Engine) -> None:
    service = service_with(engine, *["e4"] * 40)

    colors = {
        (await service.create_game(PLAYER_ID, 800, ColorChoice.RANDOM)).player_color
        for _ in range(40)
    }

    assert colors == {PlayerColor.WHITE, PlayerColor.BLACK}


async def test_move_is_followed_by_the_opponents_reply(engine: Engine) -> None:
    opponent = ScriptedOpponent("e5")
    service = GameService(engine, opponent)
    game = await service.create_game(PLAYER_ID, 1300, ColorChoice.WHITE)

    await service.play_move(game.id, "e2e4")

    with Session(engine) as session:
        stored = session.scalars(select(Move).where(Move.game_id == game.id).order_by(Move.ply))
        assert [(m.ply, m.san, m.uci) for m in stored] == [(1, "e4", "e2e4"), (2, "e5", "e7e5")]
    assert opponent.elos == [1300]


@pytest.mark.parametrize("uci", ["e2e5", "e7e5", "zz", "e7e8q", ""])
async def test_illegal_move_is_rejected_and_nothing_is_persisted(engine: Engine, uci: str) -> None:
    service = service_with(engine)
    game = await service.create_game(PLAYER_ID, 800, ColorChoice.WHITE)

    with pytest.raises(IllegalMoveError):
        await service.play_move(game.id, uci)

    assert move_count(engine, game.id) == 0


async def test_unknown_game_is_reported(engine: Engine) -> None:
    with pytest.raises(GameNotFoundError):
        await service_with(engine).play_move(99, "e2e4")


async def test_player_cannot_move_while_the_opponents_reply_is_pending(engine: Engine) -> None:
    service = service_with(engine)  # an empty script makes the opponent fail after the player moves
    game = await service.create_game(PLAYER_ID, 800, ColorChoice.WHITE)
    with pytest.raises(OpponentUnavailableError):
        await service.play_move(game.id, "e2e4")

    with pytest.raises(NotPlayersTurnError):
        await service.play_move(game.id, "e7e5")


SCHOLARS_MATE_PLAYER = ["e4", "Bc4", "Qh5", "Qxf7#"]
SCHOLARS_MATE_OPPONENT = ["e5", "Nc6", "Nf6"]


async def play_scholars_mate(service: GameService, game_id: int) -> Game:
    game = service.get_game(game_id)
    for san in SCHOLARS_MATE_PLAYER:
        game = await play_san(service, game_id, san)
    return game


async def test_checkmate_ends_the_game_without_an_opponent_reply(engine: Engine) -> None:
    opponent = ScriptedOpponent(*SCHOLARS_MATE_OPPONENT)
    service = GameService(engine, opponent)
    game = await service.create_game(PLAYER_ID, 800, ColorChoice.WHITE)

    finished = await play_scholars_mate(service, game.id)

    assert (finished.status, finished.result) == (GameStatus.FINISHED, GameResult.WHITE_WINS)
    assert finished.ended_at is not None
    assert len(opponent.elos) == 3
    with pytest.raises(GameNotActiveError):
        await service.play_move(game.id, "a2a3")


async def test_stalemate_is_a_draw(engine: Engine) -> None:
    black = ["a5", "Ra6", "h5", "Rah6", "f6", "Kf7", "Qd3", "Qh7", "Kg6"]
    white = ["e3", "Qh5", "Qxa5", "Qxc7", "h4", "Qxd7", "Qxb7", "Qxb8", "Qxc8", "Qe6"]
    service = service_with(engine, *black)
    game = await service.create_game(PLAYER_ID, 800, ColorChoice.WHITE)

    for san in white:
        game = await play_san(service, game.id, san)

    assert service.get_board(game.id).is_stalemate()
    assert (game.status, game.result) == (GameStatus.FINISHED, GameResult.DRAW)


async def test_threefold_repetition_is_a_draw(engine: Engine) -> None:
    service = service_with(engine, "Nf6", "Ng8", "Nf6", "Ng8")
    game = await service.create_game(PLAYER_ID, 800, ColorChoice.WHITE)

    for san in ["Nf3", "Ng1", "Nf3", "Ng1"]:
        game = await play_san(service, game.id, san)

    assert (game.status, game.result) == (GameStatus.FINISHED, GameResult.DRAW)


@pytest.mark.parametrize(
    ("fen", "expected"),
    [
        (chess.STARTING_FEN, None),
        ("7k/8/8/8/8/8/8/K7 w - - 0 1", GameResult.DRAW),  # insufficient material
        ("7k/8/8/8/8/8/6p1/K1R5 w - - 100 80", GameResult.DRAW),  # fifty-move rule
        ("7k/8/8/8/8/8/6p1/K1R5 w - - 98 80", None),
        ("7k/6Q1/6K1/8/8/8/8/8 b - - 0 1", GameResult.WHITE_WINS),  # checkmate
        ("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1", GameResult.DRAW),  # stalemate
    ],
)
def test_board_result(fen: str, expected: GameResult | None) -> None:
    assert board_result(chess.Board(fen)) == expected


@pytest.mark.parametrize("color", [ColorChoice.WHITE, ColorChoice.BLACK])
async def test_resign_gives_the_opponent_the_win(engine: Engine, color: ColorChoice) -> None:
    service = service_with(engine, "e4")
    game = await service.create_game(PLAYER_ID, 800, color)

    resigned = service.resign(game.id)

    expected = GameResult.BLACK_WINS if color is ColorChoice.WHITE else GameResult.WHITE_WINS
    assert (resigned.status, resigned.result) == (GameStatus.FINISHED, expected)
    assert resigned.pgn is not None
    with pytest.raises(GameNotActiveError):
        service.resign(game.id)


async def test_pgn_round_trips_through_python_chess(engine: Engine) -> None:
    service = service_with(engine, *SCHOLARS_MATE_OPPONENT)
    game = await service.create_game(PLAYER_ID, 1200, ColorChoice.WHITE)
    finished = await play_scholars_mate(service, game.id)

    parsed = chess.pgn.read_game(io.StringIO(service.get_pgn(game.id)))

    assert parsed is not None
    assert finished.pgn == service.get_pgn(game.id)
    assert parsed.headers["White"] == "Player"
    assert parsed.headers["Black"] == "Maia-3 (1200)"
    assert parsed.headers["Event"] and parsed.headers["Date"]
    assert parsed.headers["Result"] == "1-0"
    assert [m.uci() for m in parsed.mainline_moves()] == [
        m.uci() for m in service.get_board(game.id).move_stack
    ]
    assert parsed.end().board().is_checkmate()


async def test_pgn_of_an_active_game_as_black(engine: Engine) -> None:
    service = service_with(engine, "e4")
    game = await service.create_game(PLAYER_ID, 700, ColorChoice.BLACK)

    parsed = chess.pgn.read_game(io.StringIO(service.get_pgn(game.id)))

    assert parsed is not None
    assert (parsed.headers["White"], parsed.headers["Black"]) == ("Maia-3 (700)", "Player")
    assert parsed.headers["Result"] == "*"


@pytest.mark.maia
async def test_real_maia_replies_and_opens_as_white(settings: Settings, engine: Engine) -> None:
    from chess_tutor.engines.opponent import Maia3Opponent

    async with Maia3Opponent.from_settings(settings) as maia:
        service = GameService(engine, maia)
        as_black = await service.create_game(PLAYER_ID, 1100, ColorChoice.BLACK)
        assert len(service.get_board(as_black.id).move_stack) == 1

        as_white = await service.create_game(PLAYER_ID, 1100, ColorChoice.WHITE)
        await service.play_move(as_white.id, "e2e4")
        assert len(service.get_board(as_white.id).move_stack) == 2


@pytest.mark.maia
def test_app_starts_maia_and_exposes_the_game_service(settings: Settings) -> None:
    enabled = settings.model_copy(update={"maia_enabled": True})

    app = create_app(enabled)
    with TestClient(app):
        assert isinstance(app.state.game_service, GameService)


@pytest.mark.parametrize("elo", [0, 399, 3001])
async def test_out_of_range_opponent_elo_is_rejected(engine: Engine, elo: int) -> None:
    with pytest.raises(InvalidGameSettingsError):
        await service_with(engine).create_game(PLAYER_ID, elo, ColorChoice.WHITE)


async def test_concurrent_reply_requests_produce_one_opponent_move(engine: Engine) -> None:
    service = service_with(engine, "e5", "Nc6")
    game = await service.create_game(PLAYER_ID, 800, ColorChoice.WHITE)

    await asyncio.gather(
        service.play_move(game.id, "e2e4"),
        service.play_opponent_move(game.id),
    )

    assert move_count(engine, game.id) == 2
