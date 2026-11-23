import sys
from collections.abc import AsyncIterator
from pathlib import Path

import chess
import chess.engine
import pytest
import pytest_asyncio

from chess_tutor.engines.human_moves import HumanMovePredictionError, HumanMovePredictor
from chess_tutor.settings import Settings

pytestmark = [pytest.mark.maia, pytest.mark.asyncio(loop_scope="module")]

POSITIONS = [
    chess.STARTING_FEN,
    "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3",
    "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 4 4",
    "8/5pk1/6p1/8/8/6P1/5PK1/3R4 w - - 0 1",
]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def predictor() -> AsyncIterator[HumanMovePredictor]:
    async with HumanMovePredictor() as started:
        yield started


@pytest.mark.parametrize("fen", POSITIONS)
async def test_sorted_and_normalised(predictor: HumanMovePredictor, fen: str) -> None:
    board = chess.Board(fen)
    predictions = await predictor.predict_human_moves(board, 1500, 50)
    probabilities = [prediction.probability for prediction in predictions]
    assert probabilities == sorted(probabilities, reverse=True)
    assert all(0 <= probability <= 1 for probability in probabilities)
    assert sum(probabilities) == pytest.approx(1.0, abs=1e-3)
    assert {prediction.move for prediction in predictions} == set(board.legal_moves)


async def test_opening_move_is_plausible(predictor: HumanMovePredictor) -> None:
    predictions = await predictor.predict_human_moves(chess.Board(), 1500, 3)
    assert {chess.Move.from_uci("e2e4"), chess.Move.from_uci("d2d4")} & {
        prediction.move for prediction in predictions
    }


async def test_recapture_is_most_likely(predictor: HumanMovePredictor) -> None:
    # white's queen has just taken black's queen on d5; ...exd5 recaptures
    board = chess.Board("rnb1kbnr/ppp2ppp/4p3/3Q4/8/8/PPPP1PPP/RNB1KBNR b KQkq - 0 1")
    predictions = await predictor.predict_human_moves(board, 1500, 3)
    assert board.san(predictions[0].move) == "exd5"


@pytest.mark.parametrize("k", [1, 3, 7])
async def test_k_is_respected(predictor: HumanMovePredictor, k: int) -> None:
    assert len(await predictor.predict_human_moves(chess.Board(), 1500, k)) == k


async def test_k_above_legal_moves_returns_all_legal(predictor: HumanMovePredictor) -> None:
    board = chess.Board("7k/8/8/8/8/8/8/K1Q5 w - - 0 1")
    assert len(await predictor.predict_human_moves(board, 1500, 100)) == board.legal_moves.count()


async def test_top_move_matches_maia3_uci_multipv_first(predictor: HumanMovePredictor) -> None:
    # maia3-uci ranks MultiPV lines by policy, so line 1 is the policy argmax (play is sampled)
    board = chess.Board(POSITIONS[1])
    _, engine = await chess.engine.popen_uci(["maia3-uci", "--model", "5m", "--device", "cpu"])
    try:
        await engine.configure({"Elo": 1500})
        info = await engine.analyse(board, chess.engine.Limit(depth=1))
    finally:
        await engine.quit()
    top = (await predictor.predict_human_moves(board, 1500, 1))[0]
    assert info["pv"][0] == top.move


async def test_rejects_finished_game(predictor: HumanMovePredictor) -> None:
    with pytest.raises(HumanMovePredictionError, match="game is over"):
        await predictor.predict_human_moves(chess.Board("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1"), 1500, 3)


async def test_rejects_non_positive_k(predictor: HumanMovePredictor) -> None:
    with pytest.raises(HumanMovePredictionError):
        await predictor.predict_human_moves(chess.Board(), 1500, 0)


async def test_requires_start() -> None:
    with pytest.raises(HumanMovePredictionError):
        await HumanMovePredictor().predict_human_moves(chess.Board(), 1500, 3)


async def test_close_leaves_no_process() -> None:
    started = HumanMovePredictor(model="3m")
    await started.start()
    assert started._process is not None
    pid = started._process.pid
    await started.close()
    await started.close()
    assert not Path(f"/proc/{pid}").exists()
    with pytest.raises(HumanMovePredictionError):
        await started.predict_human_moves(chess.Board(), 800, 3)


async def test_failed_startup_raises_and_cleans_up() -> None:
    with pytest.raises(HumanMovePredictionError):
        await HumanMovePredictor(model="no-such-model").start()


async def test_settings_drive_the_helper() -> None:
    settings = Settings(_env_file=None, llm_provider="none", maia_model="3m")
    async with HumanMovePredictor.from_settings(settings) as started:
        assert len(await started.predict_human_moves(chess.Board(), 800, 2)) == 2


async def test_illegal_helper_move_is_rejected(tmp_path: Path) -> None:
    fake_helper = tmp_path / "fake_helper.py"
    fake_helper.write_text(
        "import sys\n"
        "print('{\"ready\": true}', flush=True)\n"
        "for _ in sys.stdin:\n"
        '    print(\'{"moves": [{"uci": "e2e5", "probability": 1.0}]}\', flush=True)\n'
    )
    async with HumanMovePredictor([sys.executable, str(fake_helper)]) as started:
        with pytest.raises(HumanMovePredictionError, match="illegal move e2e5"):
            await started.predict_human_moves(chess.Board(), 1500, 1)
