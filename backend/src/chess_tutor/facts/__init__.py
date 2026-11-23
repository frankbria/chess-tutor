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

__all__ = [
    "Fact",
    "FactPacket",
    "FactType",
    "PieceRef",
    "Side",
    "build_fact_packet",
    "discovered_attacks",
    "fork_opportunities",
    "forks",
    "hanging_pieces",
    "material_balance",
    "pins",
    "skewers",
]
