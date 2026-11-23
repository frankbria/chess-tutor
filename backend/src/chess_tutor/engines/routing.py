import chess

from chess_tutor.engines.maia2 import Maia2Opponent
from chess_tutor.engines.opponent import Maia3Opponent, OpponentEngine
from chess_tutor.engines.stockfish_limited import StockfishLimitedOpponent
from chess_tutor.settings import Settings


class RoutingOpponent:
    """The primary engine plays, except above an Elo ceiling, where the fallback takes over."""

    def __init__(self, primary: OpponentEngine, fallback: OpponentEngine, ceiling: int) -> None:
        self._primary = primary
        self._fallback = fallback
        self._ceiling = ceiling

    async def start(self) -> None:
        await self._primary.start()
        try:
            await self._fallback.start()
        except BaseException:
            await self._primary.close()
            raise

    async def close(self) -> None:
        try:
            await self._primary.close()
        finally:
            await self._fallback.close()

    async def choose_move(self, board: chess.Board, elo: int) -> chess.Move:
        engine = self._fallback if elo > self._ceiling else self._primary
        return await engine.choose_move(board, elo)


def build_opponent(settings: Settings) -> OpponentEngine:
    stockfish = StockfishLimitedOpponent.from_settings(settings)
    match settings.opponent_engine:
        case "stockfish":
            return stockfish
        case "maia2":
            primary: OpponentEngine = Maia2Opponent.from_settings(settings)
        case "maia3":
            primary = Maia3Opponent.from_settings(settings)
    return RoutingOpponent(primary, stockfish, settings.opponent_stockfish_above_elo)
