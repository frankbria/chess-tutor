"""Turn a move's delta into the concepts it shows the player missing. Pure given its inputs."""

import chess
from sqlalchemy.orm import Session

from chess_tutor.analysis.move_delta import MoveDelta
from chess_tutor.concepts.taxonomy import CONCEPTS, Concept
from chess_tutor.facts.detectors import side_of
from chess_tutor.facts.models import Fact, FactType
from chess_tutor.facts.packet import build_fact_packet
from chess_tutor.models import ConceptEvent, ConceptSource
from chess_tutor.openings.phase import GamePhase

# What the opponent's best reply creates, by fact type.
ALLOWED_CONCEPTS = {
    FactType.FORK: Concept.ALLOWED_FORK,
    FactType.MATE_IN_ONE: Concept.ALLOWED_MATE,
    FactType.MATE_THREAT: Concept.ALLOWED_MATE,
    FactType.PIN: Concept.PIN_BLINDNESS,
    FactType.SKEWER: Concept.SKEWER_BLINDNESS,
    FactType.DISCOVERED_ATTACK: Concept.DISCOVERED_ATTACK_BLINDNESS,
    FactType.HANGING_PIECE: Concept.HANGING_PIECE,
    FactType.BACK_RANK_WEAKNESS: Concept.BACK_RANK,
    FactType.OVERLOADED_DEFENDER: Concept.OVERLOADED_DEFENDER,
    FactType.TRAPPED_PIECE: Concept.TRAPPED_PIECE,
}


def _positional_concepts(facts: list[Fact], known_ids: set[str], rook_moved: bool) -> set[Concept]:
    """Concepts from the mover's own positional facts in the position after the move."""
    concepts: set[Concept] = set()
    for fact in facts:
        if (
            fact.type is FactType.DEVELOPMENT
            and fact.id.startswith("development:")
            and (fact.id != "development:centre")
        ):
            if "queen came out" in fact.summary:
                concepts.add(Concept.EARLY_QUEEN)
            if "undeveloped" in fact.summary:
                concepts.add(Concept.UNDEVELOPED_PIECES)
        elif fact.type is FactType.KING_SAFETY and "uncastled" in fact.summary:
            concepts.add(Concept.UNCASTLED_KING)
        elif fact.type is FactType.PAWN_STRUCTURE and fact.id not in known_ids:
            concepts.add(Concept.WEAK_PAWN_STRUCTURE)
        elif fact.type is FactType.ACTIVITY and fact.id.endswith(":passive_king"):
            concepts.add(Concept.KING_ACTIVITY_ENDGAME)
    # A rook move that leaves no rook on an open file or the seventh is a passive one.
    if rook_moved and not any(
        fact.type is FactType.ACTIVITY and ("rook_file" in fact.id or "rook_seventh" in fact.id)
        for fact in facts
    ):
        concepts.add(Concept.PASSIVE_ROOK_ENDGAME)
    return concepts


def tag_mistake(delta: MoveDelta, phase: GamePhase, board_before: chess.Board) -> list[Concept]:
    """The concepts behind a mistake, in taxonomy order, limited to those valid in `phase`."""
    mover = board_before.turn
    mover_side = side_of(mover)
    move = board_before.parse_san(delta.move_san)
    board_after = board_before.copy(stack=False)
    board_after.push(move)
    before = build_fact_packet(board_before).facts
    after = [fact for fact in build_fact_packet(board_after).facts if fact.side is mover_side]
    concepts: set[Concept] = set()

    concepts.update(
        ALLOWED_CONCEPTS[fact.type] for fact in delta.allowed_facts if fact.type in ALLOWED_CONCEPTS
    )
    if any(fact.type is FactType.IGNORED_THREAT for fact in delta.facts):
        concepts.add(Concept.MISSED_OPPONENT_THREAT)
    if any(fact.type is FactType.HANGING_PIECE for fact in after):
        concepts.add(Concept.HANGING_PIECE)

    best_uci = delta.best_move.uci() if delta.best_move else None
    for fact in before:
        if fact.side is not mover_side or fact.san_lines == [[delta.move_san]]:
            continue
        if fact.type is FactType.MATE_IN_ONE:
            concepts.add(Concept.MISSED_MATE)
        elif fact.id == f"fork_opportunity:{best_uci}":
            concepts.add(Concept.MISSED_FORK)

    concepts.update(
        _positional_concepts(
            [fact for fact in after if fact.type is not FactType.HANGING_PIECE],
            {fact.id for fact in before},
            board_before.piece_type_at(move.from_square) == chess.ROOK,
        )
    )
    return [
        concept for concept in Concept if concept in concepts and phase in CONCEPTS[concept].phases
    ]


def record_concepts(
    session: Session,
    player_id: int,
    game_id: int,
    ply: int,
    concepts: list[Concept],
    source: ConceptSource = ConceptSource.MOVE,
) -> list[ConceptEvent]:
    """Store one event per concept; the caller commits."""
    events = [
        ConceptEvent(
            player_id=player_id, game_id=game_id, ply=ply, concept=concept.value, source=source
        )
        for concept in concepts
    ]
    session.add_all(events)
    session.flush()
    return events
