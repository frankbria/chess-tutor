import asyncio

import chess


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
