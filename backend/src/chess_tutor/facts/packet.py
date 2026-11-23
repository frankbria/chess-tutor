import chess

from chess_tutor.facts.detectors import hanging_pieces, material_balance, side_of
from chess_tutor.facts.models import FactPacket
from chess_tutor.facts.tactics import (
    discovered_attacks,
    fork_opportunities,
    forks,
    pins,
    skewers,
)


def build_fact_packet(board: chess.Board) -> FactPacket:
    return FactPacket(
        fen=board.fen(),
        side_to_move=side_of(board.turn),
        facts=[
            material_balance(board),
            *hanging_pieces(board),
            *forks(board),
            *fork_opportunities(board),
            *pins(board),
            *skewers(board),
            *discovered_attacks(board),
        ],
    )
