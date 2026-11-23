import random
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import chess
import chess.engine
import pytest
from fastapi.testclient import TestClient
from scripted_opponent import ScriptedOpponent
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from chess_tutor.concepts.taxonomy import Concept
from chess_tutor.database import create_database_engine, run_migrations, seed_default_player
from chess_tutor.engines.human_moves import HumanMovePrediction
from chess_tutor.engines.steering import SteeringOpponent, focus_concepts_of_first_player
from chess_tutor.engines.stockfish import AnalysedLine, Budget
from chess_tutor.main import create_app
from chess_tutor.models import ConceptState, ConceptStatus
from chess_tutor.settings import Settings

# White's Bh5 and black's c5/f5 pawns: g6 attacks the player's bishop (a hanging player piece),
# cxd4 hangs black's own pawn but also gives white a fork move, h6/b6 change nothing.
HANG_FEN = "1nbqkb1r/rp1ppnpp/p7/2p2p1B/3P1P2/2P1P2P/PP4P1/RNBQK1NR b KQk - 0 7"
HANGING_MOVE = chess.Move.from_uci("g7g6")
OWN_GIFT_MOVE = chess.Move.from_uci("c5d4")  # hangs black's own pawn; creates white's fork move
QUIET_MOVE = chess.Move.from_uci("h7h6")
OTHER_QUIET_MOVE = chess.Move.from_uci("b7b6")
# Black's queen forks on g6, a fork against the player, not a chance for them.
ALLOWED_FORK_FEN = "rnb1kbnr/pp1ppppp/B1p5/8/4P1q1/P6P/1PPP1PP1/RNBQK1NR b KQkq - 4 6"
ALLOWED_FORK_MOVE = chess.Move.from_uci("g4g6")
FORK_QUIET_MOVE = chess.Move.from_uci("d7d6")


def hanging_focus() -> frozenset[Concept]:
    return frozenset({Concept.HANGING_PIECE})


def fork_focus() -> frozenset[Concept]:
    return frozenset({Concept.MISSED_FORK})


def no_focus() -> frozenset[Concept]:
    return frozenset()


class ScriptedPredictor:
    """Test double for our human-move source: a fixed ranked list."""

    def __init__(self, *moves: tuple[chess.Move, float]) -> None:
        self._predictions = [HumanMovePrediction(move, probability) for move, probability in moves]
        self.requests: list[tuple[int, int]] = []

    async def predict_human_moves(
        self, board: chess.Board, elo: int, k: int
    ) -> list[HumanMovePrediction]:
        self.requests.append((elo, k))
        return self._predictions[:k]


class BrokenPredictor:
    async def predict_human_moves(
        self, board: chess.Board, elo: int, k: int
    ) -> list[HumanMovePrediction]:
        raise RuntimeError("helper died")


class ScriptedAnalyser:
    """Test double for our analyser: the mover's centipawns per move, as the opponent's pov."""

    def __init__(self, centipawns: dict[chess.Move, int]) -> None:
        self._centipawns = centipawns

    async def analyse(
        self, board: chess.Board, budget: Budget | chess.engine.Limit, multipv: int = 1
    ) -> list[AnalysedLine]:
        mover_score = self._centipawns[board.move_stack[-1]]
        score = chess.engine.Cp(-mover_score)  # side to move is the other side
        white_score = chess.engine.PovScore(score, board.turn).white()
        return [AnalysedLine(white_score, score, [])]


def steering(
    predictor: ScriptedPredictor | BrokenPredictor,
    centipawns: dict[chess.Move, int],
    focus: Callable[[], frozenset[Concept]] = hanging_focus,
    rate: float = 1.0,
    max_eval_loss_centipawns: int = 30,
) -> tuple[SteeringOpponent, ScriptedOpponent]:
    wrapped = ScriptedOpponent(*["h6"] * 600)
    return (
        SteeringOpponent(
            wrapped,
            predictor,
            ScriptedAnalyser(centipawns),
            focus,
            rate,
            max_eval_loss_centipawns,
            random_source=random.Random(0),
        ),
        wrapped,
    )


async def picks(opponent: SteeringOpponent, count: int = 200) -> list[chess.Move]:
    return [await opponent.choose_move(chess.Board(HANG_FEN), 1500) for _ in range(count)]


async def test_a_steered_move_hangs_a_player_piece() -> None:
    predictor = ScriptedPredictor((QUIET_MOVE, 0.5), (HANGING_MOVE, 0.3), (OTHER_QUIET_MOVE, 0.2))
    opponent, wrapped = steering(
        predictor, {QUIET_MOVE: 0, HANGING_MOVE: -10, OTHER_QUIET_MOVE: 0}, rate=1.0
    )

    assert set(await picks(opponent, 20)) == {HANGING_MOVE}
    assert wrapped.elos == []
    assert predictor.requests[0] == (1500, 4)


async def test_steered_choices_follow_maias_probabilities() -> None:
    board = chess.Board(HANG_FEN)
    likely, unlikely = HANGING_MOVE, OWN_GIFT_MOVE  # the second creates the player's fork move
    predictor = ScriptedPredictor((likely, 0.6), (unlikely, 0.2), (QUIET_MOVE, 0.2))
    opponent, _ = steering(
        predictor,
        {likely: 0, unlikely: 0, QUIET_MOVE: 0},
        focus=lambda: frozenset({Concept.HANGING_PIECE, Concept.MISSED_FORK}),
        rate=1.0,
    )

    chosen = [await opponent.choose_move(board, 1500) for _ in range(400)]

    assert set(chosen) == {likely, unlikely}
    assert chosen.count(likely) / 400 == pytest.approx(0.75, abs=0.07)


async def test_only_the_configured_share_of_moves_is_steered() -> None:
    predictor = ScriptedPredictor((QUIET_MOVE, 0.5), (HANGING_MOVE, 0.3), (OTHER_QUIET_MOVE, 0.2))
    opponent, wrapped = steering(
        predictor, {QUIET_MOVE: 0, HANGING_MOVE: -10, OTHER_QUIET_MOVE: 0}, rate=0.25
    )

    steered = (await picks(opponent, 400)).count(HANGING_MOVE)

    assert steered / 400 == pytest.approx(0.25, abs=0.07)
    assert len(wrapped.elos) == 400 - steered  # every other move is the wrapped engine's own


async def test_a_zero_rate_never_steers() -> None:
    predictor = ScriptedPredictor((QUIET_MOVE, 0.5), (HANGING_MOVE, 0.5))
    opponent, wrapped = steering(predictor, {QUIET_MOVE: 0, HANGING_MOVE: 0}, rate=0.0)

    await picks(opponent, 20)

    assert len(wrapped.elos) == 20
    assert predictor.requests == []


async def test_a_move_costing_more_than_the_cap_is_never_chosen() -> None:
    predictor = ScriptedPredictor((QUIET_MOVE, 0.5), (HANGING_MOVE, 0.3), (OTHER_QUIET_MOVE, 0.2))
    costly = {QUIET_MOVE: 0, HANGING_MOVE: -31, OTHER_QUIET_MOVE: -20}
    opponent, wrapped = steering(predictor, costly, max_eval_loss_centipawns=30)

    chosen = await picks(opponent, 20)

    assert HANGING_MOVE not in chosen
    assert wrapped.elos == [1500] * 20  # no affordable motif move: the wrapped engine plays


async def test_a_move_at_the_cap_is_allowed() -> None:
    predictor = ScriptedPredictor((QUIET_MOVE, 0.5), (HANGING_MOVE, 0.3))
    opponent, _ = steering(
        predictor, {QUIET_MOVE: 0, HANGING_MOVE: -30}, max_eval_loss_centipawns=30
    )

    assert (await picks(opponent, 20)).count(HANGING_MOVE) > 10


async def test_candidates_below_the_probability_floor_are_ignored() -> None:
    predictor = ScriptedPredictor((QUIET_MOVE, 0.9), (HANGING_MOVE, 0.02))
    opponent, wrapped = steering(predictor, {QUIET_MOVE: 0, HANGING_MOVE: 0})

    assert HANGING_MOVE not in await picks(opponent, 10)
    assert len(wrapped.elos) == 10


async def test_no_focus_concepts_delegates_without_asking_maia() -> None:
    predictor = ScriptedPredictor((QUIET_MOVE, 0.5), (HANGING_MOVE, 0.5))
    opponent, wrapped = steering(predictor, {}, focus=no_focus)

    assert await opponent.choose_move(chess.Board(HANG_FEN), 1400) == QUIET_MOVE
    assert wrapped.elos == [1400]
    assert predictor.requests == []


@pytest.mark.parametrize(
    "concept", [Concept.EARLY_QUEEN, Concept.ALLOWED_FORK, Concept.PIN_BLINDNESS]
)
async def test_a_concept_without_a_motif_mapping_delegates(concept: Concept) -> None:
    predictor = ScriptedPredictor((QUIET_MOVE, 0.5), (HANGING_MOVE, 0.5))
    opponent, wrapped = steering(predictor, {}, focus=lambda: frozenset({concept}))

    await opponent.choose_move(chess.Board(HANG_FEN), 1400)

    assert wrapped.elos == [1400]
    assert predictor.requests == []


async def test_a_failing_predictor_falls_back_to_the_wrapped_engine() -> None:
    opponent, wrapped = steering(BrokenPredictor(), {})

    assert await opponent.choose_move(chess.Board(HANG_FEN), 1500) == QUIET_MOVE
    assert wrapped.elos == [1500]


async def test_no_candidate_creating_the_motif_delegates() -> None:
    predictor = ScriptedPredictor((QUIET_MOVE, 0.6), (OTHER_QUIET_MOVE, 0.4))
    opponent, wrapped = steering(predictor, {QUIET_MOVE: 0, OTHER_QUIET_MOVE: 0})

    await opponent.choose_move(chess.Board(HANG_FEN), 1500)

    assert wrapped.elos == [1500]


async def test_a_mating_candidate_is_scored_without_asking_stockfish() -> None:
    board = chess.Board("6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1")
    opponent = SteeringOpponent(
        ScriptedOpponent(),
        ScriptedPredictor(),
        ScriptedAnalyser({}),
        no_focus,
    )

    assert await opponent._evaluation(board, chess.Move.from_uci("a1a8")) == 10_000


async def test_lifecycle_is_the_wrapped_engines() -> None:
    wrapped = ScriptedOpponent()
    async with SteeringOpponent(wrapped, ScriptedPredictor(), ScriptedAnalyser({}), no_focus):
        pass


async def test_a_move_hanging_the_opponents_own_piece_is_not_steered_toward() -> None:
    predictor = ScriptedPredictor((QUIET_MOVE, 0.5), (OWN_GIFT_MOVE, 0.5))
    opponent, wrapped = steering(predictor, {QUIET_MOVE: 0, OWN_GIFT_MOVE: 0})

    assert OWN_GIFT_MOVE not in await picks(opponent, 20)
    assert wrapped.elos == [1500] * 20


async def test_a_fork_against_the_player_is_not_steered_toward_for_missed_fork() -> None:
    predictor = ScriptedPredictor((FORK_QUIET_MOVE, 0.5), (ALLOWED_FORK_MOVE, 0.5))
    opponent, wrapped = steering(
        predictor, {FORK_QUIET_MOVE: 0, ALLOWED_FORK_MOVE: 0}, focus=fork_focus
    )

    chosen = [await opponent.choose_move(chess.Board(ALLOWED_FORK_FEN), 1500) for _ in range(20)]

    assert ALLOWED_FORK_MOVE not in chosen
    assert wrapped.elos == [1500] * 20


async def test_a_move_giving_the_player_a_fork_is_steered_toward_for_missed_fork() -> None:
    predictor = ScriptedPredictor((QUIET_MOVE, 0.5), (OWN_GIFT_MOVE, 0.5))
    opponent, _ = steering(predictor, {QUIET_MOVE: 0, OWN_GIFT_MOVE: 0}, focus=fork_focus)

    assert set(await picks(opponent, 20)) == {OWN_GIFT_MOVE}


@pytest.fixture
def database(tmp_path: Path) -> Engine:
    settings = Settings(_env_file=None, llm_provider="none", database_path=tmp_path / "t.db")
    run_migrations(settings)
    engine = create_database_engine(settings)
    seed_default_player(engine)
    return engine


def test_focus_concepts_read_only_focus_rows(database: Engine) -> None:
    with Session(database) as session:
        for concept, status in [
            ("allowed_fork", ConceptStatus.FOCUS),
            ("hanging_piece", ConceptStatus.RECURRING),
            ("retired_concept", ConceptStatus.FOCUS),
        ]:
            session.add(
                ConceptState(
                    player_id=1,
                    concept=concept,
                    state=status,
                    count=3,
                    recency_score=2.0,
                    games_since_last_seen=0,
                    last_processed_game_id=0,
                    updated_at=datetime(2026, 1, 1),
                )
            )
        session.commit()

    assert focus_concepts_of_first_player(database) == {Concept.ALLOWED_FORK}


async def test_a_motif_already_on_the_board_is_not_steered_toward() -> None:
    # black's e5 pawn already hangs: h6 and a6 leave it so, which creates nothing new
    board = chess.Board("rnbqkbnr/pppp1ppp/8/4p2Q/8/8/PPPPPPPP/RNB1KBNR b KQkq - 0 1")
    predictor = ScriptedPredictor(
        (chess.Move.from_uci("h7h6"), 0.6), (chess.Move.from_uci("a7a6"), 0.4)
    )
    wrapped = ScriptedOpponent("h6")
    opponent = SteeringOpponent(
        wrapped,
        predictor,
        ScriptedAnalyser({chess.Move.from_uci("h7h6"): 0, chess.Move.from_uci("a7a6"): 0}),
        lambda: frozenset({Concept.HANGING_PIECE}),
        random_source=random.Random(0),
    )

    await opponent.choose_move(board, 1500)

    assert wrapped.elos == [1500]


@pytest.mark.maia
@pytest.mark.parametrize("enabled", [True, False])
def test_the_lifespan_wraps_the_opponent_only_when_steering_is_enabled(
    tmp_path: Path, enabled: bool
) -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="none",
        database_path=tmp_path / "t.db",
        opponent_steering_enabled=enabled,
    )
    app = create_app(settings)

    with TestClient(app):
        assert isinstance(app.state.opponent, SteeringOpponent) is enabled


async def test_above_the_elo_ceiling_the_routed_engine_plays_unsteered() -> None:
    # Above the Stockfish routing threshold the opponent is Stockfish, not Maia: steering
    # (which is built on Maia's move predictions) must step aside.
    predictor = ScriptedPredictor((QUIET_MOVE, 0.5), (HANGING_MOVE, 0.3), (OTHER_QUIET_MOVE, 0.2))
    wrapped = ScriptedOpponent(*["h6"] * 10)
    opponent = SteeringOpponent(
        wrapped,
        predictor,
        ScriptedAnalyser({QUIET_MOVE: 0, HANGING_MOVE: -10, OTHER_QUIET_MOVE: 0}),
        hanging_focus,
        1.0,
        30,
        max_steered_elo=2000,
        random_source=random.Random(0),
    )

    await opponent.choose_move(chess.Board(HANG_FEN), 2400)

    assert wrapped.elos == [2400]
    assert predictor.requests == []
