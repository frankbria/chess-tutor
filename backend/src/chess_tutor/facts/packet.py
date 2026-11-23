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
from chess_tutor.facts.threats import (
    back_rank_weaknesses,
    mate_threats,
    mates_in_one,
    overloaded_defenders,
    trapped_pieces,
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
            *back_rank_weaknesses(board),
            *mates_in_one(board),
            *mate_threats(board),
            *overloaded_defenders(board),
            *trapped_pieces(board),
        ],
    )
