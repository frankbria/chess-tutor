import asyncio
import random
import time
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum

import chess
import chess.pgn
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from chess_tutor.engines.opponent import OpponentEngine
from chess_tutor.engines.think_time import OpponentPacing
from chess_tutor.games.clock import ClockState
from chess_tutor.models import (
    Game,
    GameMode,
    GameResult,
    GameStatus,
    Move,
    Player,
    PlayerColor,
    TimeControl,
)
from chess_tutor.openings import lookup_opening
from chess_tutor.rating.history import record_rating
from chess_tutor.rating.performance import record_performance
from chess_tutor.settings import Thresholds

OPPONENT_ENGINE_NAME = "maia3"


class ColorChoice(StrEnum):
    WHITE = "white"
    BLACK = "black"
    RANDOM = "random"


class GameError(Exception):
    pass


class GameNotFoundError(GameError):
    pass


class GameNotActiveError(GameError):
    pass


class IllegalMoveError(GameError):
    pass


class InvalidGameSettingsError(GameError):
    pass


class OpponentUnavailableError(GameError):
    """The opponent engine failed to reply. The player's move is kept; retry the reply later."""


MINIMUM_OPPONENT_ELO = 400
MAXIMUM_OPPONENT_ELO = 3000
ELO_STEP = 50


class NotPlayersTurnError(GameError):
    pass


def board_result(board: chess.Board) -> GameResult | None:
    """The result if the game is over (claimable draws count as over), else None."""
    outcome = board.outcome(claim_draw=True)
    return None if outcome is None else GameResult(outcome.result())


def wall_clock_ms() -> int:
    return time.time_ns() // 1_000_000


def chess_color(color: PlayerColor) -> chess.Color:
    return chess.WHITE if color is PlayerColor.WHITE else chess.BLACK


class GameService:
    def __init__(
        self,
        engine: Engine,
        opponent: OpponentEngine,
        pacing: OpponentPacing | None = None,
        thresholds: Thresholds | None = None,
        clock: Callable[[], int] = wall_clock_ms,
    ) -> None:
        self._clock = clock
        self._thresholds = thresholds or Thresholds()
        self._engine = engine
        self._opponent = opponent
        self._pacing = pacing
        # Serialises moves so a double-submitted move can't race past the turn check.
        self._move_lock = asyncio.Lock()

    async def create_game(
        self,
        player_id: int,
        opponent_elo: int | None = None,
        color: ColorChoice = ColorChoice.RANDOM,
        mode: GameMode = GameMode.REGULAR,
        time_control: TimeControl | None = None,
    ) -> Game:
        if time_control is not None and mode is not GameMode.REGULAR:
            raise InvalidGameSettingsError("only Regular mode can be timed")
        if opponent_elo is None:
            opponent_elo = self._default_opponent_elo(player_id, mode)
        if not MINIMUM_OPPONENT_ELO <= opponent_elo <= MAXIMUM_OPPONENT_ELO:
            raise InvalidGameSettingsError(
                f"opponent Elo must be between {MINIMUM_OPPONENT_ELO} and {MAXIMUM_OPPONENT_ELO}"
            )
        if color is ColorChoice.RANDOM:
            color = random.choice([ColorChoice.WHITE, ColorChoice.BLACK])
        with Session(self._engine, expire_on_commit=False) as session:
            game = Game(
                player_id=player_id,
                mode=mode,
                player_color=PlayerColor(color.value),
                opponent_engine=OPPONENT_ENGINE_NAME,
                opponent_elo=opponent_elo,
                status=GameStatus.ACTIVE,
            )
            if time_control is not None:
                game.time_control = time_control
                game.white_ms = game.black_ms = time_control.base_ms
                game.clock_started_ms = self._clock()
            session.add(game)
            session.commit()
        return await self.play_opponent_move(game.id)

    def _default_opponent_elo(self, player_id: int, mode: GameMode) -> int:
        """The player's rating, plus the stretch offset in tutor modes, to the nearest 50."""
        with Session(self._engine) as session:
            target = session.get_one(Player, player_id).rating
        if mode is not GameMode.REGULAR:
            target += self._thresholds.tutor_stretch_offset
        rounded = round(target / ELO_STEP) * ELO_STEP
        return min(MAXIMUM_OPPONENT_ELO, max(MINIMUM_OPPONENT_ELO, rounded))

    async def play_move(self, game_id: int, uci: str) -> Game:
        """Apply the player's move, then let the opponent answer if the game goes on."""
        async with self._move_lock:
            with Session(self._engine, expire_on_commit=False) as session:
                game = self._active_game(session, game_id)
                board = self._board(session, game_id)
                if board.turn != chess_color(game.player_color):
                    raise NotPlayersTurnError("it is the opponent's turn")
                move = self._parse_legal_move(board, uci)
                self._push(session, game, board, move)
                session.commit()
        return await self.play_opponent_move(game_id)

    async def play_opponent_move(self, game_id: int) -> Game:
        """Play the opponent's move if it is due; call again after an OpponentUnavailableError."""
        await self._think(game_id)
        async with self._move_lock:
            return await self._reply(game_id)

    async def _think(self, game_id: int) -> None:
        """Humanised wait outside the lock; _reply re-reads the game so a resign meanwhile wins."""
        if self._pacing is None:
            return
        with Session(self._engine, expire_on_commit=False) as session:
            game = self._get(session, game_id)
            board = self._board(session, game_id)
            if game.status is not GameStatus.ACTIVE or board.turn == chess_color(game.player_color):
                return
        await self._pacing.pause(board, game.opponent_elo)

    async def _reply(self, game_id: int) -> Game:
        with Session(self._engine, expire_on_commit=False) as session:
            game = self._get(session, game_id)
            board = self._board(session, game_id)
            if game.status is not GameStatus.ACTIVE or board.turn == chess_color(game.player_color):
                return game
            try:
                move = await self._opponent.choose_move(board, game.opponent_elo)
            # The engine is an external process: any failure there means no reply.
            except Exception as error:
                raise OpponentUnavailableError("the opponent engine did not reply") from error
            session.refresh(game)  # a flag or resignation may have ended the game meanwhile
            if game.status is not GameStatus.ACTIVE:
                return game
            self._push(session, game, board, move)
            session.commit()
            return game

    def resign(self, game_id: int) -> Game:
        with Session(self._engine, expire_on_commit=False) as session:
            game = self._active_game(session, game_id)
            winner = (
                GameResult.BLACK_WINS
                if game.player_color is PlayerColor.WHITE
                else GameResult.WHITE_WINS
            )
            self._finish(session, game, winner)
            session.commit()
            return game

    def check_flag(self, game_id: int) -> Game:
        """End the game on time if the side to move has run out; otherwise return it unchanged."""
        with Session(self._engine, expire_on_commit=False) as session:
            game = self._get(session, game_id)
            if game.status is GameStatus.ACTIVE and game.time_control is not None:
                mover = self._board(session, game_id).turn
                if self._remaining_ms(game, mover) <= 0:
                    self._flag(session, game, mover)
                    session.commit()
            return game

    def clock_state(self, game: Game, turn: chess.Color) -> ClockState | None:
        if game.time_control is None or game.white_ms is None or game.black_ms is None:
            return None
        running = game.status is GameStatus.ACTIVE

        def shown(side: chess.Color, stored: int) -> int:
            return max(0, self._remaining_ms(game, side) if running and turn == side else stored)

        white_ms = shown(chess.WHITE, game.white_ms)
        black_ms = shown(chess.BLACK, game.black_ms)
        side = PlayerColor.WHITE if turn == chess.WHITE else PlayerColor.BLACK
        return ClockState(white_ms=white_ms, black_ms=black_ms, running=side if running else None)

    def get_game(self, game_id: int) -> Game:
        with Session(self._engine, expire_on_commit=False) as session:
            return self._get(session, game_id)

    def list_games(self, status: GameStatus | None = None) -> list[Game]:
        query = select(Game).order_by(Game.id.desc())
        if status is not None:
            query = query.where(Game.status == status)
        with Session(self._engine, expire_on_commit=False) as session:
            return list(session.scalars(query))

    def get_board(self, game_id: int) -> chess.Board:
        with Session(self._engine) as session:
            self._get(session, game_id)
            return self._board(session, game_id)

    def get_pgn(self, game_id: int) -> str:
        with Session(self._engine) as session:
            return self._render_pgn(session, self._get(session, game_id))

    def _get(self, session: Session, game_id: int) -> Game:
        game = session.get(Game, game_id)
        if game is None:
            raise GameNotFoundError(f"game {game_id} does not exist")
        return game

    def _active_game(self, session: Session, game_id: int) -> Game:
        game = self._get(session, game_id)
        if game.status is not GameStatus.ACTIVE:
            raise GameNotActiveError(f"game {game_id} is already finished")
        return game

    def _moves(self, session: Session, game_id: int) -> list[Move]:
        return list(session.scalars(select(Move).where(Move.game_id == game_id).order_by(Move.ply)))

    def _board(self, session: Session, game_id: int) -> chess.Board:
        # The stored UCI moves are the only source of truth for the position.
        board = chess.Board()
        for stored in self._moves(session, game_id):
            board.push_uci(stored.uci)
        return board

    def _parse_legal_move(self, board: chess.Board, uci: str) -> chess.Move:
        try:
            move = chess.Move.from_uci(uci)
        except ValueError as error:
            raise IllegalMoveError(f"'{uci}' is not a UCI move") from error
        if move not in board.legal_moves:
            raise IllegalMoveError(f"{uci} is not legal in {board.fen()}")
        return move

    def _remaining_ms(self, game: Game, side: chess.Color) -> int:
        """Stored time for the side, less the running time when it is the side whose clock ticks."""
        assert game.white_ms is not None and game.black_ms is not None
        assert game.clock_started_ms is not None
        stored = game.white_ms if side == chess.WHITE else game.black_ms
        return stored - (self._clock() - game.clock_started_ms)

    def _flag(self, session: Session, game: Game, loser: chess.Color) -> None:
        if loser == chess.WHITE:
            game.white_ms, result = 0, GameResult.BLACK_WINS
        else:
            game.black_ms, result = 0, GameResult.WHITE_WINS
        self._finish(session, game, result, on_time=True)

    def _push(self, session: Session, game: Game, board: chess.Board, move: chess.Move) -> None:
        mover = board.turn
        if game.time_control is not None:
            remaining = self._remaining_ms(game, mover)
            if remaining <= 0:
                self._flag(session, game, mover)
                return
            remaining += game.time_control.increment_ms
            if mover == chess.WHITE:
                game.white_ms = remaining
            else:
                game.black_ms = remaining
            game.clock_started_ms = self._clock()
        session.add(
            Move(
                game_id=game.id,
                ply=len(board.move_stack) + 1,
                san=board.san(move),
                uci=move.uci(),
                fen_before=board.fen(),
            )
        )
        board.push(move)
        opening = lookup_opening(board)
        if opening is not None:
            game.opening_eco, game.opening_name = opening
        result = board_result(board)
        if result is not None:
            self._finish(session, game, result)

    def _finish(
        self, session: Session, game: Game, result: GameResult, on_time: bool = False
    ) -> None:
        session.flush()
        game.status = GameStatus.FINISHED
        game.ended_on_time = on_time
        game.result = result
        game.ended_at = datetime.now(UTC)
        game.pgn = self._render_pgn(session, game)
        record_performance(session, game)
        record_rating(session, game, self._thresholds)

    def _render_pgn(self, session: Session, game: Game) -> str:
        player = session.get_one(Player, game.player_id)
        opponent_name = f"Maia-3 ({game.opponent_elo})"
        player_is_white = game.player_color is PlayerColor.WHITE
        pgn_game = chess.pgn.Game()
        pgn_game.headers["Event"] = "Chess Tutor game"
        pgn_game.headers["Date"] = game.started_at.strftime("%Y.%m.%d")
        pgn_game.headers["White"] = player.display_name if player_is_white else opponent_name
        pgn_game.headers["Black"] = opponent_name if player_is_white else player.display_name
        pgn_game.headers["Result"] = game.result.value if game.result else "*"
        node: chess.pgn.GameNode = pgn_game
        for stored in self._moves(session, game.id):
            node = node.add_variation(chess.Move.from_uci(stored.uci))
        return str(pgn_game)
