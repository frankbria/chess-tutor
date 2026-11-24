"""Active tutor mode: analyse a proposed move before it is locked in.

The analysis keeps the best move out of everything this module returns; the coach's hint ladder
(which reads the stored proposal) must go on withholding it until lock-in.
"""

import chess

from chess_tutor.analysis.move_delta import move_delta
from chess_tutor.engines.stockfish import StockfishPool
from chess_tutor.games.service import GameService
from chess_tutor.models import MoveProposal


class ActiveTutor:
    def __init__(self, service: GameService, pool: StockfishPool) -> None:
        self._service = service
        self._pool = pool

    async def propose(self, game_id: int, uci: str) -> MoveProposal:
        proposal = await self._service.propose_move(game_id, uci)
        try:
            board = self._service.get_board(game_id)
            delta = await move_delta(board, chess.Move.from_uci(uci), self._pool)
        except BaseException:  # incl. cancellation: a stuck proposal would answer every retry 409
            self._service.withdraw_proposal(proposal.id, uci)
            raise
        self._service.record_proposal_analysis(
            proposal.id, uci, delta.win_probability_before, delta.win_probability_after
        )
        proposal.win_probability_before = delta.win_probability_before
        proposal.win_probability_after = delta.win_probability_after
        return proposal
