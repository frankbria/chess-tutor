import asyncio
import random
import time
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

import chess
import chess.pgn
from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session

from chess_tutor.concepts.state import refresh_concept_states
from chess_tutor.engines.opponent import OpponentEngine
from chess_tutor.engines.think_time import OpponentPacing
from chess_tutor.games.clock import ClockState
from chess_tutor.models import (
    Game,
    GameMode,
    GameResult,
    GameStatus,
    Move,
    MoveProposal,
    Player,
    PlayerColor,
    TimeControl,
)
from chess_tutor.openings import lookup_opening
from chess_tutor.rating.history import record_rating
from chess_tutor.rating.performance import record_performance
from chess_tutor.settings import Thresholds

OPPONENT_ENGINE_NAME = "maia3"


class PlayerMoveObserver(Protocol):
    def player_moved(self, game: Game, ply: int) -> None:
        """Called under the move lock right after the player's move is committed."""

    def game_finished(self, game_id: int) -> None:
        """Called under the move lock right after a game's end is flushed, before its commit."""

    def is_coaching(self, game_id: int) -> bool:
        """True while work on the game's moves is pending; it then refreshes concepts itself."""


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


class WrongModeError(GameError):
    pass


class NoProposalError(GameError):
    """Lock-in or rethink with no proposed move waiting."""


class ProposalRequiredError(GameError):
    """An Active tutor game takes its moves through propose and lock-in."""


class ProposalPendingError(GameError):
    """A move is already proposed: rethink it before proposing another."""


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
        observer: PlayerMoveObserver | None = None,
    ) -> None:
        self._observer = observer
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
        if self.get_game(game_id).mode is GameMode.ACTIVE:
            raise ProposalRequiredError("propose the move, then lock it in")
        return await self._commit_player_move(game_id, uci)

    async def lock_in(self, game_id: int) -> Game:
        """Commit exactly the proposed move (Active mode), then let the opponent answer."""
        return await self._commit_player_move(game_id, None)

    async def _commit_player_move(self, game_id: int, uci: str | None) -> Game:
        """`uci` None means the pending proposal; its rethink count is stored on the move."""
        async with self._move_lock:
            with Session(self._engine, expire_on_commit=False) as session:
                game = self._active_game(session, game_id)
                board = self._board(session, game_id)
                if board.turn != chess_color(game.player_color):
                    raise NotPlayersTurnError("it is the opponent's turn")
                proposed_attempts = 0
                if uci is None:
                    self._require_active_mode(game)
                    proposal, uci = self._pending_proposal(
                        session, game_id, len(board.move_stack) + 1
                    )
                    proposed_attempts = proposal.rethinks
                    session.delete(proposal)
                move = self._parse_legal_move(board, uci)
                plies_before = len(board.move_stack)
                self._push(session, game, board, move, proposed_attempts)
                session.commit()
                if self._observer is not None and len(board.move_stack) > plies_before:
                    self._observer.player_moved(game, len(board.move_stack))
        return await self.play_opponent_move(game_id)

    async def propose_move(self, game_id: int, uci: str) -> MoveProposal:
        """Store the move as proposed (Active mode); the game itself does not change."""
        async with self._move_lock:
            with Session(self._engine, expire_on_commit=False) as session:
                game = self._active_game(session, game_id)
                self._require_active_mode(game)
                board = self._board(session, game_id)
                if board.turn != chess_color(game.player_color):
                    raise NotPlayersTurnError("it is the opponent's turn")
                move = self._parse_legal_move(board, uci)
                ply = len(board.move_stack) + 1
                proposal = self._proposal_row(session, game_id)
                if proposal is None:
                    proposal = MoveProposal(
                        game_id=game_id, ply=ply, rethinks=0, san=board.san(move)
                    )
                    session.add(proposal)
                elif proposal.uci is not None:
                    raise ProposalPendingError("rethink the proposed move before proposing again")
                proposal.uci, proposal.san = uci, board.san(move)
                session.commit()
                return proposal

    async def rethink(self, game_id: int) -> int:
        """Discard the proposed move and count the rethink; returns the rethinks so far."""
        async with self._move_lock:
            with Session(self._engine) as session:
                game = self._active_game(session, game_id)
                self._require_active_mode(game)
                ply = len(self._board(session, game_id).move_stack) + 1
                proposal, _ = self._pending_proposal(session, game.id, ply)
                proposal.uci = None
                proposal.win_probability_before = proposal.win_probability_after = None
                proposal.rethinks += 1
                session.commit()
                return proposal.rethinks

    def record_proposal_analysis(
        self,
        proposal_id: int,
        uci: str,
        win_probability_before: float,
        win_probability_after: float,
    ) -> None:
        """Keep the analysis only while that same move is still the proposal."""
        with Session(self._engine) as session:
            proposal = session.get(MoveProposal, proposal_id)
            if proposal is None or proposal.uci != uci:
                return
            proposal.win_probability_before = win_probability_before
            proposal.win_probability_after = win_probability_after
            session.commit()

    def withdraw_proposal(self, proposal_id: int, uci: str) -> None:
        """Back out a proposal whose analysis failed, keeping the rethink count."""
        with Session(self._engine) as session:
            proposal = session.get(MoveProposal, proposal_id)
            if proposal is not None and proposal.uci == uci:
                proposal.uci = None
                session.commit()

    def _require_active_mode(self, game: Game) -> None:
        if game.mode is not GameMode.ACTIVE:
            raise WrongModeError("only Active tutor games take proposed moves")

    def _proposal_row(self, session: Session, game_id: int) -> MoveProposal | None:
        return session.scalars(select(MoveProposal).where(MoveProposal.game_id == game_id)).first()

    def _pending_proposal(
        self, session: Session, game_id: int, ply: int
    ) -> tuple[MoveProposal, str]:
        proposal = self._proposal_row(session, game_id)
        if proposal is None or proposal.uci is None or proposal.ply != ply:
            raise NoProposalError("there is no proposed move")
        return proposal, proposal.uci

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

    async def check_flag(self, game_id: int) -> Game:
        """End the game on time if the side to move has run out; otherwise return it unchanged."""
        async with self._move_lock:  # a flag must not interleave with a move's read-check-write
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

    def _push(
        self,
        session: Session,
        game: Game,
        board: chess.Board,
        move: chess.Move,
        proposed_attempts: int = 0,
    ) -> None:
        if game.status is not GameStatus.ACTIVE:
            raise GameNotActiveError(f"game {game.id} is already finished")
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
                proposed_attempts=proposed_attempts,
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
        session.execute(delete(MoveProposal).where(MoveProposal.game_id == game.id))
        game.ended_on_time = on_time
        game.result = result
        game.ended_at = datetime.now(UTC)
        game.pgn = self._render_pgn(session, game)
        record_performance(session, game)
        record_rating(session, game, self._thresholds)
        if self._observer is None or not self._observer.is_coaching(game.id):
            refresh_concept_states(
                session, game.player_id, game.id, game.ended_at, self._thresholds.concept_state
            )
        if self._observer is not None:
            self._observer.game_finished(game.id)

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
