"""Deterministic position facts: the only chess truth the coach may cite."""

from chess_tutor.facts.detectors import hanging_pieces, material_balance
from chess_tutor.facts.models import Fact, FactPacket, FactType, PieceRef, Side
from chess_tutor.facts.packet import build_fact_packet
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

__all__ = [
    "Fact",
    "FactPacket",
    "FactType",
    "PieceRef",
    "Side",
    "back_rank_weaknesses",
    "build_fact_packet",
    "discovered_attacks",
    "fork_opportunities",
    "forks",
    "hanging_pieces",
    "mate_threats",
    "mates_in_one",
    "material_balance",
    "overloaded_defenders",
    "pins",
    "skewers",
    "trapped_pieces",
]
