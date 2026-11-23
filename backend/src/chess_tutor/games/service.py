import random
from datetime import UTC, datetime
from enum import StrEnum

import chess
import chess.pgn
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from chess_tutor.engines.opponent import OpponentEngine
from chess_tutor.models import (
    Game,
    GameMode,
    GameResult,
    GameStatus,
    Move,
    Player,
    PlayerColor,
)

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


class NotPlayersTurnError(GameError):
    pass


def board_result(board: chess.Board) -> GameResult | None:
    """The result if the game is over (claimable draws count as over), else None."""
    outcome = board.outcome(claim_draw=True)
    return None if outcome is None else GameResult(outcome.result())


def chess_color(color: PlayerColor) -> chess.Color:
    return chess.WHITE if color is PlayerColor.WHITE else chess.BLACK


class GameService:
    def __init__(self, engine: Engine, opponent: OpponentEngine) -> None:
        self._engine = engine
        self._opponent = opponent

    async def create_game(
        self,
        player_id: int,
        opponent_elo: int,
        color: ColorChoice = ColorChoice.RANDOM,
        mode: GameMode = GameMode.REGULAR,
    ) -> Game:
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
            session.add(game)
            session.commit()
        return await self.play_opponent_move(game.id)

    async def play_move(self, game_id: int, uci: str) -> Game:
        """Apply the player's move, then let the opponent answer if the game goes on."""
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
        """Play the opponent's move if due, else do nothing; also retries a failed reply."""
        with Session(self._engine, expire_on_commit=False) as session:
            game = self._get(session, game_id)
            board = self._board(session, game_id)
            if game.status is not GameStatus.ACTIVE or board.turn == chess_color(game.player_color):
                return game
            move = await self._opponent.choose_move(board, game.opponent_elo)
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

    def get_game(self, game_id: int) -> Game:
        with Session(self._engine, expire_on_commit=False) as session:
            return self._get(session, game_id)

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

    def _push(self, session: Session, game: Game, board: chess.Board, move: chess.Move) -> None:
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
        result = board_result(board)
        if result is not None:
            self._finish(session, game, result)

    def _finish(self, session: Session, game: Game, result: GameResult) -> None:
        session.flush()
        game.status = GameStatus.FINISHED
        game.result = result
        game.ended_at = datetime.now(UTC)
        game.pgn = self._render_pgn(session, game)

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
