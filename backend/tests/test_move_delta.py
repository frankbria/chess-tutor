from collections.abc import AsyncIterator

import chess
import chess.engine
import pytest

from chess_tutor.analysis.classification import analyse_position
from chess_tutor.analysis.move_delta import (
    THREAT_MIN_SWING,
    MoveDelta,
    _new_facts_after,
    _threat_swing,
    move_delta,
    threat_probe,
)
from chess_tutor.engines.stockfish import Budget, StockfishPool
from chess_tutor.facts.models import Fact, FactType, Side
from chess_tutor.facts.packet import build_fact_packet
from chess_tutor.settings import Settings

BISHOP_ATTACKED_BY_PAWN = "rnbqkbnr/p1pp1ppp/8/1p2p3/2B1P3/8/PPPP1PPP/RNBQK1NR w KQkq - 0 3"
KNIGHT_FORK_AVAILABLE = "3qk2r/pp3ppp/8/8/1n6/8/PP3PPP/R2QK1N1 w - - 0 1"
KING_IN_CHECK = "4r1k1/8/8/8/8/8/5PPP/4K3 w - - 0 1"


@pytest.fixture
async def pool() -> AsyncIterator[StockfishPool]:
    settings = Settings(_env_file=None, llm_provider="none", stockfish_pool_size=1)
    async with StockfishPool(settings.stockfish_path, 1) as started:
        yield started


def assert_parallel(facts: list[Fact]) -> None:
    for fact in facts:
        assert len(fact.pieces) <= len(fact.squares)
        assert [piece.square for piece in fact.pieces] == fact.squares[: len(fact.pieces)]


def fact_of(delta: MoveDelta, fact_type: FactType) -> Fact:
    return next(fact for fact in delta.facts if fact.type is fact_type)


async def test_probe_reports_a_piece_the_opponent_can_win(pool: StockfishPool) -> None:
    report = await threat_probe(chess.Board(BISHOP_ATTACKED_BY_PAWN), pool)

    assert report.threatening_side is Side.BLACK
    assert report.san == "bxc4"
    assert report.win_probability_after > report.win_probability_before + 10


async def test_probe_reports_the_facts_the_threat_would_create(pool: StockfishPool) -> None:
    report = await threat_probe(
        chess.Board("3qk2r/pp3ppp/8/8/1n4Q1/8/PP3PPP/R3K1N1 w - - 0 1"), pool
    )

    assert report.san == "Nc2+"
    assert any(fact.type is FactType.FORK for fact in report.facts)
    assert_parallel(report.facts)


async def test_probe_finds_nothing_in_a_quiet_position(pool: StockfishPool) -> None:
    report = await threat_probe(chess.Board(), pool)

    assert report.move is None
    assert report.facts == []


async def test_probe_is_skipped_when_in_check(pool: StockfishPool) -> None:
    report = await threat_probe(chess.Board(KING_IN_CHECK), pool)

    assert report.move is None
    assert report.win_probability_before == report.win_probability_after == 0.0  # never analysed


async def test_ignoring_a_threatened_piece_is_flagged(pool: StockfishPool) -> None:
    delta = await move_delta(
        chess.Board(BISHOP_ATTACKED_BY_PAWN), chess.Move.from_uci("g1f3"), pool
    )

    ignored = fact_of(delta, FactType.IGNORED_THREAT)
    assert ignored.san_lines == [["bxc4"]]
    assert ignored.side is Side.BLACK
    assert delta.win_probability_after < delta.win_probability_before
    assert_parallel(delta.facts)


async def test_moving_the_threatened_piece_is_not_flagged(pool: StockfishPool) -> None:
    delta = await move_delta(
        chess.Board(BISHOP_ATTACKED_BY_PAWN), chess.Move.from_uci("c4b5"), pool
    )

    assert all(fact.type is not FactType.IGNORED_THREAT for fact in delta.facts)


async def test_a_careless_move_allowing_a_knight_fork_reports_it(pool: StockfishPool) -> None:
    delta = await move_delta(chess.Board(KNIGHT_FORK_AVAILABLE), chess.Move.from_uci("d1g4"), pool)

    allows = fact_of(delta, FactType.ALLOWS)
    assert allows.san_lines[0][0] == "Nc2+"
    assert delta.reply_san == "Nc2+"
    assert any(fact.type is FactType.FORK for fact in delta.allowed_facts)
    assert next(f for f in delta.allowed_facts if f.type is FactType.FORK).side is Side.BLACK
    already_known = {
        fact.id for fact in build_fact_packet(chess.Board(KNIGHT_FORK_AVAILABLE)).facts
    }
    assert not already_known & {fact.id for fact in delta.allowed_facts}
    assert all(fact.id != "hanging:g7" for fact in delta.allowed_facts)  # black's own weakness
    assert_parallel(delta.facts + delta.allowed_facts)


async def test_move_delta_works_when_in_check(pool: StockfishPool) -> None:
    delta = await move_delta(chess.Board(KING_IN_CHECK), chess.Move.from_uci("e1d2"), pool)

    assert all(fact.type is not FactType.IGNORED_THREAT for fact in delta.facts)
    assert delta.best_move is not None


async def test_a_quiet_good_move_allows_nothing(pool: StockfishPool) -> None:
    delta = await move_delta(chess.Board(), chess.Move.from_uci("e2e4"), pool)

    assert delta.facts == []
    assert delta.best_move is not None
    assert delta.best_line and delta.best_line[0] == delta.best_move_san


async def test_best_move_is_not_baked_into_the_facts(pool: StockfishPool) -> None:
    delta = await move_delta(
        chess.Board(BISHOP_ATTACKED_BY_PAWN), chess.Move.from_uci("g1f3"), pool
    )

    assert delta.best_move_san is not None
    for fact in delta.facts:
        assert delta.best_move_san not in fact.summary


async def test_a_move_that_ends_the_game_has_no_reply(pool: StockfishPool) -> None:
    board = chess.Board("6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1")

    delta = await move_delta(board, chess.Move.from_uci("a1a8"), pool)

    assert delta.reply is None
    assert delta.win_probability_after == 100.0


async def test_a_blunder_that_still_saves_the_threatened_piece_is_not_an_ignored_threat(
    pool: StockfishPool,
) -> None:
    delta = await move_delta(
        chess.Board(BISHOP_ATTACKED_BY_PAWN), chess.Move.from_uci("c4f7"), pool
    )

    assert all(fact.type is not FactType.IGNORED_THREAT for fact in delta.facts)
    assert any(fact.type is FactType.ALLOWS for fact in delta.facts)


async def test_a_threat_ignored_for_counterplay_is_still_flagged(pool: StockfishPool) -> None:
    delta = await move_delta(
        chess.Board(BISHOP_ATTACKED_BY_PAWN), chess.Move.from_uci("d1h5"), pool
    )

    assert fact_of(delta, FactType.IGNORED_THREAT).san_lines == [["bxc4"]]


async def test_a_threat_outside_the_top_replies_is_evaluated_directly(pool: StockfishPool) -> None:
    board_after = chess.Board(BISHOP_ATTACKED_BY_PAWN)
    board_after.push(chess.Move.from_uci("g1f3"))
    best_replies = await analyse_position(pool, board_after, Budget.STANDARD)

    pointless = await _threat_swing(board_after, chess.Move.from_uci("a7a6"), best_replies, pool)
    winning = await _threat_swing(board_after, chess.Move.from_uci("b5c4"), [], pool)

    assert pointless < -THREAT_MIN_SWING
    assert winning == 0.0  # no alternatives to compare with


async def test_a_mated_side_to_move_scores_as_a_loss(pool: StockfishPool) -> None:
    board = chess.Board("R5k1/5ppp/8/8/8/8/5PPP/6K1 b - - 0 1")

    line = (await analyse_position(pool, board, Budget.QUICK))[0]

    assert line.side_to_move_score == -chess.engine.MateGiven
    assert line.white_score == chess.engine.MateGiven


def test_facts_already_in_the_reference_position_are_not_new() -> None:
    forking = chess.Board("3qk2r/pp3ppp/8/8/6Q1/8/PPn2PPP/R3K1N1 b - - 0 1")
    before_the_fork = chess.Board("3qk2r/pp3ppp/8/8/1n4Q1/8/PP3PPP/R3K1N1 b - - 0 1")
    quiet_reply = chess.Move.from_uci("a7a6")

    credited = _new_facts_after(before_the_fork, forking, quiet_reply, Side.BLACK)
    not_credited = _new_facts_after(forking, forking, quiet_reply, Side.BLACK)

    assert any(fact.type is FactType.FORK for fact in credited)
    assert all(fact.type is not FactType.FORK for fact in not_credited)
