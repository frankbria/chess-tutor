"""The fixed concept taxonomy: every concept a mistake can be tagged with, the phases it
shows up in, a human label, and the lichess puzzle themes that practise it."""

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from chess_tutor.openings.phase import GamePhase

ALL_PHASES = (GamePhase.OPENING, GamePhase.MIDDLEGAME, GamePhase.ENDGAME)
AFTER_OPENING = (GamePhase.MIDDLEGAME, GamePhase.ENDGAME)


class Concept(StrEnum):
    HANGING_PIECE = "hanging_piece"
    MISSED_FORK = "missed_fork"
    ALLOWED_FORK = "allowed_fork"
    MISSED_OPPONENT_THREAT = "missed_opponent_threat"
    BACK_RANK = "back_rank"
    PIN_BLINDNESS = "pin_blindness"
    SKEWER_BLINDNESS = "skewer_blindness"
    DISCOVERED_ATTACK_BLINDNESS = "discovered_attack_blindness"
    MISSED_MATE = "missed_mate"
    ALLOWED_MATE = "allowed_mate"
    OVERLOADED_DEFENDER = "overloaded_defender"
    TRAPPED_PIECE = "trapped_piece"
    EARLY_QUEEN = "early_queen"
    UNDEVELOPED_PIECES = "undeveloped_pieces"
    UNCASTLED_KING = "uncastled_king"
    WEAK_PAWN_STRUCTURE = "weak_pawn_structure"
    PASSIVE_ROOK_ENDGAME = "passive_rook_endgame"
    KING_ACTIVITY_ENDGAME = "king_activity_endgame"


@dataclass(frozen=True)
class ConceptInfo:
    phases: tuple[GamePhase, ...]
    label: str
    practice_themes: tuple[str, ...]  # lichess puzzle theme slugs


CONCEPTS = MappingProxyType(
    {
        Concept.HANGING_PIECE: ConceptInfo(
            ALL_PHASES, "Leaving pieces undefended", ("hangingPiece",)
        ),
        Concept.MISSED_FORK: ConceptInfo(ALL_PHASES, "Missing a fork", ("fork",)),
        Concept.ALLOWED_FORK: ConceptInfo(ALL_PHASES, "Allowing a fork", ("fork",)),
        Concept.MISSED_OPPONENT_THREAT: ConceptInfo(
            ALL_PHASES, "Missing the opponent's threat", ("defensiveMove",)
        ),
        Concept.BACK_RANK: ConceptInfo(AFTER_OPENING, "Back-rank weakness", ("backRankMate",)),
        Concept.PIN_BLINDNESS: ConceptInfo(ALL_PHASES, "Overlooking pins", ("pin",)),
        Concept.SKEWER_BLINDNESS: ConceptInfo(ALL_PHASES, "Overlooking skewers", ("skewer",)),
        Concept.DISCOVERED_ATTACK_BLINDNESS: ConceptInfo(
            ALL_PHASES, "Overlooking discovered attacks", ("discoveredAttack",)
        ),
        Concept.MISSED_MATE: ConceptInfo(ALL_PHASES, "Missing a checkmate", ("mateIn1", "mate")),
        Concept.ALLOWED_MATE: ConceptInfo(
            ALL_PHASES, "Allowing a checkmate", ("mate", "defensiveMove")
        ),
        Concept.OVERLOADED_DEFENDER: ConceptInfo(
            AFTER_OPENING, "Overloaded defenders", ("deflection", "capturingDefender")
        ),
        Concept.TRAPPED_PIECE: ConceptInfo(ALL_PHASES, "Trapped pieces", ("trappedPiece",)),
        Concept.EARLY_QUEEN: ConceptInfo(
            (GamePhase.OPENING,), "Bringing the queen out too early", ("opening",)
        ),
        Concept.UNDEVELOPED_PIECES: ConceptInfo(
            (GamePhase.OPENING,), "Leaving pieces undeveloped", ("opening",)
        ),
        Concept.UNCASTLED_KING: ConceptInfo(
            (GamePhase.OPENING, GamePhase.MIDDLEGAME),
            "Leaving the king uncastled",
            ("castling", "exposedKing"),
        ),
        Concept.WEAK_PAWN_STRUCTURE: ConceptInfo(
            (GamePhase.MIDDLEGAME,), "Weakening the pawn structure", ("middlegame", "quietMove")
        ),
        Concept.PASSIVE_ROOK_ENDGAME: ConceptInfo(
            (GamePhase.ENDGAME,), "Passive rooks in endgames", ("rookEndgame",)
        ),
        Concept.KING_ACTIVITY_ENDGAME: ConceptInfo(
            (GamePhase.ENDGAME,), "Keeping the king active in endgames", ("endgame", "pawnEndgame")
        ),
    }
)
