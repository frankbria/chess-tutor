from collections.abc import AsyncIterator
from pathlib import Path

import chess
import chess.engine
import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from chess_tutor.analysis.classification import (
    MoveClassification,
    assess_played_move,
    classify_move,
    game_accuracy,
    move_accuracy,
    win_probability,
)
from chess_tutor.analysis.recording import classify_stored_move
from chess_tutor.database import create_database_engine, run_migrations, seed_default_player
from chess_tutor.engines.stockfish import AnalysedLine, StockfishPool
from chess_tutor.models import Game, GameMode, GameStatus, Move, PlayerColor
from chess_tutor.settings import ClassificationThresholds, Settings


def cp(centipawns: int) -> chess.engine.Score:
    return chess.engine.Cp(centipawns)


def line(uci: str, centipawns: int, color: chess.Color = chess.WHITE) -> AnalysedLine:
    """An analysed line scored from `color`'s point of view."""
    score = cp(centipawns if color == chess.WHITE else -centipawns)
    return AnalysedLine(
        white_score=score, side_to_move_score=cp(centipawns), pv=[chess.Move.from_uci(uci)]
    )


def centipawns_for_win_probability(target: float) -> int:
    return next(c for c in range(-1000, 1001) if win_probability(cp(c)) >= target)


# --- win probability and accuracy formulas -------------------------------------------------


def test_win_probability_matches_lichess_curve() -> None:
    assert win_probability(cp(0)) == pytest.approx(50.0)
    assert win_probability(cp(100)) == pytest.approx(59.1, abs=0.1)
    assert win_probability(cp(800)) == pytest.approx(95.0, abs=0.1)
    assert win_probability(cp(-300)) == pytest.approx(100 - win_probability(cp(300)))


def test_mate_is_a_certain_win_or_loss() -> None:
    assert win_probability(chess.engine.Mate(3)) == 100.0
    assert win_probability(chess.engine.Mate(-3)) == 0.0
    assert win_probability(chess.engine.MateGiven) == 100.0
    assert win_probability(-chess.engine.MateGiven) == 0.0


def test_move_accuracy_matches_lichess_formula() -> None:
    assert move_accuracy(0) == pytest.approx(100.0, abs=0.01)
    assert move_accuracy(10) == pytest.approx(63.5, abs=0.1)
    assert move_accuracy(50) == pytest.approx(8.5, abs=0.1)
    assert move_accuracy(100) == 0.0


def test_game_accuracy_blends_arithmetic_and_harmonic_means() -> None:
    assert game_accuracy([]) == 100.0
    assert game_accuracy([0, 0, 0]) == pytest.approx(100.0, abs=0.01)
    # accuracies 100 and ~63.5: arithmetic ~81.7, harmonic ~77.7
    assert game_accuracy([0, 10]) == pytest.approx(79.7, abs=0.2)
    assert game_accuracy([100, 100]) == pytest.approx(0.0, abs=0.01)


# --- loss thresholds ----------------------------------------------------------------------

START = chess.Board()
BEST_MOVE = "e2e4"
PLAYED = chess.Move.from_uci("a2a3")
THRESHOLDS = ClassificationThresholds()


def classify_with_loss(loss: float) -> MoveClassification:
    """Mover is white at 50%; the played move (not in the lines) ends `loss` points lower."""
    played_cp = centipawns_for_win_probability(50 - loss)
    return classify_move(
        [line(BEST_MOVE, 0), line("d2d4", -300)], START, PLAYED, after=line("e7e5", played_cp)
    )


@pytest.mark.parametrize(
    ("loss", "expected"),
    [
        (0.5, MoveClassification.BEST),
        (3.0, MoveClassification.GOOD),
        (4.7, MoveClassification.GOOD),
        (5.3, MoveClassification.INACCURACY),
        (9.7, MoveClassification.INACCURACY),
        (10.3, MoveClassification.MISTAKE),
        (19.7, MoveClassification.MISTAKE),
        (20.3, MoveClassification.BLUNDER),
        (45.0, MoveClassification.BLUNDER),
    ],
)
def test_loss_maps_to_label_around_each_threshold(
    loss: float, expected: MoveClassification
) -> None:
    assert classify_with_loss(loss) is expected


def test_thresholds_are_configurable() -> None:
    strict = ClassificationThresholds(inaccuracy=1.0, mistake=2.0, blunder=3.0)
    result = classify_move(
        [line(BEST_MOVE, 0), line("d2d4", -300)], START, PLAYED, strict, after=line("e7e5", -60)
    )
    assert result is MoveClassification.BLUNDER


def test_move_below_best_loss_counts_as_best_even_if_not_the_engines_first_choice() -> None:
    result = classify_move([line(BEST_MOVE, 10), line("a2a3", 5)], START, PLAYED)
    assert result is MoveClassification.BEST


def test_black_moves_are_scored_from_blacks_side() -> None:
    board = chess.Board()
    board.push_uci("e2e4")
    best = line("e7e5", 0, chess.BLACK)
    blunder = line("g8h6", 0, chess.BLACK)
    after_blunder = AnalysedLine(white_score=cp(600), side_to_move_score=cp(600), pv=[])
    result = classify_move([best, blunder], board, chess.Move.from_uci("a7a6"), after=after_blunder)
    assert result is MoveClassification.BLUNDER


def test_move_outside_analysis_without_after_is_rejected() -> None:
    with pytest.raises(ValueError, match="after"):
        classify_move([line(BEST_MOVE, 0)], START, PLAYED)


# --- won position ------------------------------------------------------------------------


def test_two_hundred_centipawn_drop_in_a_won_position_is_not_a_blunder() -> None:
    result = classify_move(
        [line(BEST_MOVE, 800), line("d2d4", 780)], START, PLAYED, after=line("e7e5", 600)
    )
    assert result is MoveClassification.GOOD
    assert win_probability(cp(800)) - win_probability(cp(600)) < THRESHOLDS.inaccuracy


def test_same_drop_in_an_equal_position_is_a_mistake_or_worse() -> None:
    result = classify_move(
        [line(BEST_MOVE, 0), line("d2d4", -10)], START, PLAYED, after=line("e7e5", -200)
    )
    assert result in (MoveClassification.MISTAKE, MoveClassification.BLUNDER)


# --- great: only moves ------------------------------------------------------------------


def test_best_move_when_alternatives_lose_a_lot_is_great() -> None:
    best = chess.Move.from_uci(BEST_MOVE)
    result = classify_move([line(BEST_MOVE, 0), line("d2d4", -300)], START, best)
    assert result is MoveClassification.GREAT


def test_best_move_with_a_close_alternative_is_just_best() -> None:
    best = chess.Move.from_uci(BEST_MOVE)
    assert (
        classify_move([line(BEST_MOVE, 0), line("d2d4", -20)], START, best)
        is MoveClassification.BEST
    )


def test_great_gap_boundary() -> None:
    best = chess.Move.from_uci(BEST_MOVE)
    just_under = centipawns_for_win_probability(50 - 14.5)
    just_over = centipawns_for_win_probability(50 - 15.5)
    assert (
        classify_move([line(BEST_MOVE, 0), line("d2d4", just_under)], START, best)
        is MoveClassification.BEST
    )
    assert (
        classify_move([line(BEST_MOVE, 0), line("d2d4", just_over)], START, best)
        is MoveClassification.GREAT
    )


def test_a_single_legal_move_is_not_great() -> None:
    best = chess.Move.from_uci(BEST_MOVE)
    assert classify_move([line(BEST_MOVE, 0)], START, best) is MoveClassification.BEST


# --- brilliant: sound sacrifices ---------------------------------------------------------

# White's knight on e5 can go to f7 where it hangs to the king and rook, winning nothing back
# materially (a knight for a pawn); a rook on a1 behind keeps the line closed.
SACRIFICE_FEN = "r4rk1/ppp2ppp/8/4N3/8/8/PPP2PPP/R3R1K1 w - - 0 1"


def test_sound_sacrifice_is_brilliant() -> None:
    board = chess.Board(SACRIFICE_FEN)
    sacrifice = chess.Move.from_uci("e5f7")
    lines = [
        AnalysedLine(white_score=cp(150), side_to_move_score=cp(150), pv=[sacrifice]),
        line("e1e2", 120),
    ]
    assert classify_move(lines, board, sacrifice) is MoveClassification.BRILLIANT


def test_sacrifice_that_loses_the_evaluation_is_not_brilliant() -> None:
    board = chess.Board(SACRIFICE_FEN)
    sacrifice = chess.Move.from_uci("e5f7")
    result = classify_move(
        [line("e1e2", 150), line("a2a3", 140)], board, sacrifice, after=line("f8f7", -400)
    )
    assert result is MoveClassification.BLUNDER


def test_sacrifice_in_a_lost_position_is_not_brilliant() -> None:
    board = chess.Board(SACRIFICE_FEN)
    sacrifice = chess.Move.from_uci("e5f7")
    lines = [
        AnalysedLine(white_score=cp(-500), side_to_move_score=cp(-500), pv=[sacrifice]),
        line("e1e2", -520),
    ]
    assert classify_move(lines, board, sacrifice) is MoveClassification.BEST


def test_winning_an_undefended_pawn_is_not_a_sacrifice() -> None:
    board = chess.Board("4k3/8/8/3p4/8/8/3R4/4K3 w - - 0 1")
    capture = chess.Move.from_uci("d2d5")  # wins a pawn, nothing recaptures
    lines = [AnalysedLine(white_score=cp(100), side_to_move_score=cp(100), pv=[capture])]
    assert classify_move(lines, board, capture) is MoveClassification.BEST


# --- real Stockfish ---------------------------------------------------------------------


@pytest.fixture
async def pool() -> AsyncIterator[StockfishPool]:
    settings = Settings(_env_file=None, llm_provider="none", stockfish_pool_size=1)
    async with StockfishPool(settings.stockfish_path, 1) as started:
        yield started


QUEEN_HANGS_TO_BISHOP = "rnbqkbnr/pppp1ppp/8/4P3/8/8/PPP1PPPP/RNBQKBNR b KQkq - 0 2"


async def test_hanging_the_queen_is_a_blunder(pool: StockfishPool) -> None:
    assessment = await assess_played_move(
        pool, chess.Board(QUEEN_HANGS_TO_BISHOP), chess.Move.from_uci("d8g5")
    )

    assert assessment.classification is MoveClassification.BLUNDER
    assert assessment.win_probability_after < assessment.win_probability_before


async def test_a_book_move_is_best_or_good(pool: StockfishPool) -> None:
    assessment = await assess_played_move(pool, chess.Board(), chess.Move.from_uci("e2e4"))

    assert assessment.classification in (MoveClassification.BEST, MoveClassification.GOOD)


async def test_mating_move_outside_the_top_lines_is_scored_as_a_win(pool: StockfishPool) -> None:
    board = chess.Board(
        "6k1/5ppp/8/8/8/8/QRRR1PPP/6K1 w - - 0 1"
    )  # five mates, only three fit the lines
    mates = [chess.Move.from_uci(uci) for uci in ("a2a8", "b2b8", "c2c8", "d2d8")]

    assessments = [await assess_played_move(pool, board, mate) for mate in mates]

    assert all(a.win_probability_after == 100.0 for a in assessments)
    assert all(a.classification is not MoveClassification.BLUNDER for a in assessments)


@pytest.fixture
def database(tmp_path: Path) -> Engine:
    settings = Settings(_env_file=None, llm_provider="none", database_path=tmp_path / "t.db")
    run_migrations(settings)
    engine = create_database_engine(settings)
    seed_default_player(engine)
    return engine


async def test_classification_is_stored_on_the_move(pool: StockfishPool, database: Engine) -> None:
    with Session(database) as session:
        game = Game(
            player_id=1,
            mode=GameMode.REGULAR,
            player_color=PlayerColor.BLACK,
            opponent_engine="maia3",
            opponent_elo=1500,
            status=GameStatus.ACTIVE,
        )
        session.add(game)
        session.flush()
        session.add(
            Move(game_id=game.id, ply=2, san="Qg5", uci="d8g5", fen_before=QUEEN_HANGS_TO_BISHOP)
        )
        session.commit()
        game_id = game.id

    await classify_stored_move(database, pool, game_id, 2)

    with Session(database) as session:
        stored = session.query(Move).filter_by(game_id=game_id, ply=2).one()
        assert stored.classification == "blunder"
        assert stored.eval_before is not None and stored.eval_after is not None
        assert stored.eval_after < stored.eval_before


def test_even_trade_is_not_a_sacrifice() -> None:
    board = chess.Board("4k3/8/3p4/4r3/8/8/4R3/4K3 w - - 0 1")
    trade = chess.Move.from_uci("e2e5")  # rook takes rook, the d6 pawn recaptures
    lines = [AnalysedLine(white_score=cp(0), side_to_move_score=cp(0), pv=[trade])]
    assert classify_move(lines, board, trade) is MoveClassification.BEST
