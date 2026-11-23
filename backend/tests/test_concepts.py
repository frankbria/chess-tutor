from collections.abc import AsyncIterator, Callable, Iterator

import chess
import pytest
from scripted_opponent import ScriptedOpponent
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from chess_tutor.analysis.move_delta import MoveDelta, move_delta
from chess_tutor.concepts.tagger import _positional_concepts, record_concepts, tag_mistake
from chess_tutor.concepts.taxonomy import CONCEPTS, Concept
from chess_tutor.database import create_database_engine, run_migrations, seed_default_player
from chess_tutor.engines.stockfish import StockfishPool
from chess_tutor.facts.models import Fact, FactType, Side
from chess_tutor.facts.positional import development, king_safety
from chess_tutor.games.service import ColorChoice, GameService
from chess_tutor.models import ConceptEvent, ConceptSource, GameMode
from chess_tutor.openings.phase import GamePhase
from chess_tutor.settings import Settings

PLAYER_ID = 1
# Every theme key in lichess's PuzzleTheme.scala (lila master), checked 2026-11-23.
LICHESS_THEMES = frozenset(
    {
        "advancedPawn",
        "advantage",
        "anastasiaMate",
        "arabianMate",
        "attackingF2F7",
        "attraction",
        "backRankMate",
        "balestraMate",
        "bishopEndgame",
        "blindSwineMate",
        "bodenMate",
        "capturingDefender",
        "castling",
        "clearance",
        "collinearMove",
        "cornerMate",
        "crushing",
        "defensiveMove",
        "deflection",
        "discoveredAttack",
        "discoveredCheck",
        "doubleBishopMate",
        "doubleCheck",
        "dovetailMate",
        "enPassant",
        "endgame",
        "epauletteMate",
        "equality",
        "exposedKing",
        "fork",
        "hangingPiece",
        "hookMate",
        "interference",
        "intermezzo",
        "killBoxMate",
        "kingsideAttack",
        "knightEndgame",
        "long",
        "master",
        "masterVsMaster",
        "mate",
        "mateIn1",
        "mateIn2",
        "mateIn3",
        "mateIn4",
        "mateIn5",
        "middlegame",
        "mix",
        "morphysMate",
        "oneMove",
        "opening",
        "operaMate",
        "pawnEndgame",
        "pillsburysMate",
        "pin",
        "promotion",
        "queenEndgame",
        "queenRookEndgame",
        "queensideAttack",
        "quietMove",
        "rookEndgame",
        "sacrifice",
        "short",
        "skewer",
        "smotheredMate",
        "superGM",
        "swallowstailMate",
        "trappedPiece",
        "triangleMate",
        "underPromotion",
        "veryLong",
        "vukovicMate",
        "xRayAttack",
        "zugzwang",
    }
)
FORK_ALLOWED = "3qk2r/pp3ppp/8/8/1n4Q1/8/PP3PPP/R3K1N1 w - - 0 1"
BISHOP_ATTACKED_BY_PAWN = "rnbqkbnr/p1pp1ppp/8/1p2p3/2B1P3/8/PPPP1PPP/RNBQK1NR w KQkq - 0 3"
MATE_IN_ONE = "6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1"
FORK_AVAILABLE = "q3k3/8/8/1N6/8/8/8/4K3 w - - 0 1"


@pytest.fixture
async def pool() -> AsyncIterator[StockfishPool]:
    settings = Settings(_env_file=None, llm_provider="none", stockfish_pool_size=1)
    async with StockfishPool(settings.stockfish_path, 1) as started:
        yield started


def delta_with(san: str, allowed: list[FactType] | None = None, ignored: bool = False) -> MoveDelta:
    def fact(fact_type: FactType, side: Side) -> Fact:
        return Fact(id=fact_type.value, type=fact_type, side=side, summary=fact_type.value)

    return MoveDelta(
        move_san=san,
        win_probability_before=60,
        win_probability_after=30,
        best_move=None,
        best_move_san=None,
        best_line=[],
        reply=None,
        reply_san=None,
        allowed_facts=[fact(kind, Side.BLACK) for kind in allowed or []],
        facts=[fact(FactType.IGNORED_THREAT, Side.BLACK)] if ignored else [],
    )


def test_every_concept_has_a_phase_a_label_and_verified_practice_themes() -> None:
    assert set(CONCEPTS) == set(Concept)
    for info in CONCEPTS.values():
        assert info.phases
        assert info.label
        assert info.practice_themes
        assert set(info.practice_themes) <= LICHESS_THEMES


@pytest.mark.parametrize(
    ("fact_type", "concept"),
    [
        (FactType.FORK, Concept.ALLOWED_FORK),
        (FactType.MATE_IN_ONE, Concept.ALLOWED_MATE),
        (FactType.MATE_THREAT, Concept.ALLOWED_MATE),
        (FactType.PIN, Concept.PIN_BLINDNESS),
        (FactType.SKEWER, Concept.SKEWER_BLINDNESS),
        (FactType.DISCOVERED_ATTACK, Concept.DISCOVERED_ATTACK_BLINDNESS),
        (FactType.HANGING_PIECE, Concept.HANGING_PIECE),
        (FactType.BACK_RANK_WEAKNESS, Concept.BACK_RANK),
        (FactType.OVERLOADED_DEFENDER, Concept.OVERLOADED_DEFENDER),
        (FactType.TRAPPED_PIECE, Concept.TRAPPED_PIECE),
    ],
)
def test_what_the_reply_creates_names_the_concept(fact_type: FactType, concept: Concept) -> None:
    delta = delta_with("e4", allowed=[fact_type])

    assert tag_mistake(delta, GamePhase.MIDDLEGAME, chess.Board()) == [concept]


def test_an_ignored_threat_is_a_missed_opponent_threat() -> None:
    delta = delta_with("e4", ignored=True)

    assert tag_mistake(delta, GamePhase.OPENING, chess.Board()) == [Concept.MISSED_OPPONENT_THREAT]


def test_concepts_outside_the_phase_are_dropped() -> None:
    delta = delta_with("e4", allowed=[FactType.BACK_RANK_WEAKNESS])

    assert tag_mistake(delta, GamePhase.OPENING, chess.Board()) == []


def test_a_quiet_move_has_no_concepts() -> None:
    assert tag_mistake(delta_with("e4"), GamePhase.OPENING, chess.Board()) == []


@pytest.mark.parametrize(
    ("fen", "san", "phase", "expected"),
    [
        (
            "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
            "Qh5",
            GamePhase.OPENING,
            Concept.EARLY_QUEEN,
        ),
        (
            "r1bqk2r/pppp1ppp/2n2n2/4p3/1b2P3/2N2N2/PPPP1PPP/R1BQKB1R w KQkq - 4 5",
            "Nd5",
            GamePhase.OPENING,
            Concept.UNDEVELOPED_PIECES,
        ),
        (
            "r1bqk2r/ppp2ppp/2n2n2/8/8/2N2N2/PPP2PPP/R1BQK2R w KQkq - 0 10",
            "Nd2",
            GamePhase.MIDDLEGAME,
            Concept.UNCASTLED_KING,
        ),
        (
            "r1bqkbnr/pppp1ppp/2B5/4p3/4P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 0 1",
            "bxc6",
            GamePhase.MIDDLEGAME,
            Concept.WEAK_PAWN_STRUCTURE,
        ),
        (
            "6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1",
            "Rf1",
            GamePhase.ENDGAME,
            Concept.PASSIVE_ROOK_ENDGAME,
        ),
        (
            "6k1/5ppp/8/8/8/8/5PPP/6K1 w - - 0 1",
            "Kf1",
            GamePhase.ENDGAME,
            Concept.KING_ACTIVITY_ENDGAME,
        ),
    ],
)
def test_positional_mistakes_tag_the_phase_concept(
    fen: str, san: str, phase: GamePhase, expected: Concept
) -> None:
    assert expected in tag_mistake(delta_with(san), phase, chess.Board(fen))


def test_a_rook_move_onto_an_open_file_is_not_passive() -> None:
    board = chess.Board("2r3k1/5ppp/8/8/8/8/5PPP/2R3K1 w - - 0 1")

    assert Concept.PASSIVE_ROOK_ENDGAME not in tag_mistake(
        delta_with("Rc2"), GamePhase.ENDGAME, board
    )


async def test_allowing_a_fork_and_ignoring_the_threat(pool: StockfishPool) -> None:
    board = chess.Board(FORK_ALLOWED)
    delta = await move_delta(board, board.parse_san("Qg3"), pool)

    concepts = tag_mistake(delta, GamePhase.MIDDLEGAME, board)

    assert Concept.ALLOWED_FORK in concepts
    assert Concept.MISSED_OPPONENT_THREAT in concepts


async def test_leaving_a_bishop_attacked_is_a_missed_threat_and_a_hanging_piece(
    pool: StockfishPool,
) -> None:
    board = chess.Board(BISHOP_ATTACKED_BY_PAWN)
    delta = await move_delta(board, board.parse_san("Nf3"), pool)

    assert tag_mistake(delta, GamePhase.OPENING, board) == [
        Concept.HANGING_PIECE,
        Concept.MISSED_OPPONENT_THREAT,
    ]


async def test_missing_a_mate_in_one(pool: StockfishPool) -> None:
    board = chess.Board(MATE_IN_ONE)
    delta = await move_delta(board, board.parse_san("h3"), pool)

    assert Concept.MISSED_MATE in tag_mistake(delta, GamePhase.ENDGAME, board)


async def test_playing_the_mate_is_not_a_missed_mate(pool: StockfishPool) -> None:
    board = chess.Board(MATE_IN_ONE)
    delta = await move_delta(board, board.parse_san("Ra8#"), pool)

    assert Concept.MISSED_MATE not in tag_mistake(delta, GamePhase.ENDGAME, board)


async def test_missing_a_fork(pool: StockfishPool) -> None:
    board = chess.Board(FORK_AVAILABLE)
    delta = await move_delta(board, board.parse_san("Kd2"), pool)

    assert delta.best_move_san == "Nc7+"
    assert Concept.MISSED_FORK in tag_mistake(delta, GamePhase.ENDGAME, board)


async def test_playing_the_fork_is_not_a_missed_fork(pool: StockfishPool) -> None:
    board = chess.Board(FORK_AVAILABLE)
    delta = await move_delta(board, board.parse_san("Nc7+"), pool)

    assert Concept.MISSED_FORK not in tag_mistake(delta, GamePhase.ENDGAME, board)


@pytest.fixture
def engine(settings: Settings) -> Iterator[Engine]:
    run_migrations(settings)
    database_engine = create_database_engine(settings)
    seed_default_player(database_engine)
    yield database_engine
    database_engine.dispose()


async def test_concept_events_are_written_for_a_game(engine: Engine, pool: StockfishPool) -> None:
    game = await GameService(engine, ScriptedOpponent()).create_game(
        PLAYER_ID, 1200, ColorChoice.WHITE, GameMode.PASSIVE
    )
    board = chess.Board(FORK_ALLOWED)
    delta = await move_delta(board, board.parse_san("Qg3"), pool)
    concepts = tag_mistake(delta, GamePhase.MIDDLEGAME, board)

    with Session(engine) as session:
        record_concepts(session, PLAYER_ID, game.id, 5, concepts)
        record_concepts(
            session, PLAYER_ID, game.id, 6, [Concept.EARLY_QUEEN], ConceptSource.REASONING
        )
        session.commit()

    with Session(engine) as session:
        rows = session.scalars(select(ConceptEvent).order_by(ConceptEvent.id)).all()
        assert [row.concept for row in rows[:-1]] == [concept.value for concept in concepts]
        assert {row.ply for row in rows[:-1]} == {5}
        assert {row.source for row in rows[:-1]} == {ConceptSource.MOVE}
        assert (rows[-1].ply, rows[-1].source) == (6, ConceptSource.REASONING)
        assert all(row.player_id == PLAYER_ID and row.game_id == game.id for row in rows)


def test_playing_one_of_several_mates_is_not_a_missed_mate() -> None:
    board = chess.Board("6k1/8/6K1/8/8/8/8/Q6R w - - 0 1")
    mates = [move for move in board.legal_moves if _gives_mate(board, move)]
    assert len(mates) >= 2

    for mate in mates:
        assert Concept.MISSED_MATE not in tag_mistake(
            delta_with(board.san(mate)), GamePhase.ENDGAME, board
        )
    assert Concept.MISSED_MATE in tag_mistake(delta_with("Kf6"), GamePhase.ENDGAME, board)


def _gives_mate(board: chess.Board, move: chess.Move) -> bool:
    board.push(move)
    mated = board.is_checkmate()
    board.pop()
    return mated


def test_a_missing_best_move_cannot_be_a_missed_fork() -> None:
    board = chess.Board(FORK_AVAILABLE)

    assert Concept.MISSED_FORK not in tag_mistake(delta_with("Kd2"), GamePhase.ENDGAME, board)


@pytest.mark.parametrize(
    ("fen", "detector", "expected"),
    [
        (
            "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
            development,
            Concept.EARLY_QUEEN,
        ),
        (
            "r1bqkb1r/pppp1ppp/2n2n2/4p3/4P3/2NP1N2/PPP2PPP/R1BQKB1R w KQkq - 0 8",
            development,
            Concept.UNDEVELOPED_PIECES,
        ),
        (
            "r1bqk2r/ppp2ppp/2n2n2/8/8/2N2N2/PPP2PPP/R1BQK2R w KQkq - 0 10",
            king_safety,
            Concept.UNCASTLED_KING,
        ),
    ],
)
def test_real_detector_output_is_recognised(
    fen: str, detector: Callable[[chess.Board], list[Fact]], expected: Concept
) -> None:
    board = chess.Board(fen)
    if expected is Concept.EARLY_QUEEN:
        board.push_san("Qh5")
        board.push_san("Nc6")
    facts = [fact for fact in detector(board) if fact.side is Side.WHITE]

    assert expected in _positional_concepts(facts, set(), rook_moved=False)
