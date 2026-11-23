import chess
import pytest
from scripted_opponent import ScriptedOpponent

from chess_tutor.engines.maia2 import Maia2Opponent
from chess_tutor.engines.opponent import Maia3Opponent
from chess_tutor.engines.routing import RoutingOpponent, build_opponent
from chess_tutor.engines.stockfish_limited import StockfishLimitedOpponent
from chess_tutor.settings import Settings


class _FailingStart(ScriptedOpponent):
    async def start(self) -> None:
        raise OpponentStartError


class OpponentStartError(RuntimeError):
    pass


class _TrackedOpponent(ScriptedOpponent):
    started = closed = False

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize(
    ("elo", "expected"), [(1500, "primary"), (2000, "primary"), (2001, "fallback")]
)
async def test_delegates_by_elo(elo: int, expected: str) -> None:
    primary, fallback = ScriptedOpponent("e4"), ScriptedOpponent("d4")
    router = RoutingOpponent(primary, fallback, ceiling=2000)
    move = await router.choose_move(chess.Board(), elo)
    assert move == chess.Move.from_uci("e2e4" if expected == "primary" else "d2d4")
    assert (primary.elos, fallback.elos) == (([elo], []) if expected == "primary" else ([], [elo]))


async def test_start_and_close_reach_both_engines() -> None:
    primary, fallback = _TrackedOpponent(), _TrackedOpponent()
    router = RoutingOpponent(primary, fallback, ceiling=2000)
    await router.start()
    await router.close()
    assert (primary.started, fallback.started, primary.closed, fallback.closed) == (True,) * 4


async def test_failed_fallback_start_closes_the_primary() -> None:
    primary = _TrackedOpponent()
    router = RoutingOpponent(primary, _FailingStart(), ceiling=2000)
    with pytest.raises(OpponentStartError):
        await router.start()
    assert primary.closed


def test_default_selects_maia3_routed_to_stockfish() -> None:
    opponent = build_opponent(Settings(_env_file=None, llm_provider="none"))
    assert isinstance(opponent, RoutingOpponent)
    assert isinstance(opponent._primary, Maia3Opponent)
    assert isinstance(opponent._fallback, StockfishLimitedOpponent)
    assert opponent._ceiling == 2000


def test_maia2_selection_and_custom_ceiling() -> None:
    opponent = build_opponent(
        Settings(
            _env_file=None,
            llm_provider="none",
            opponent_engine="maia2",
            opponent_stockfish_above_elo=1800,
        )
    )
    assert isinstance(opponent, RoutingOpponent)
    assert isinstance(opponent._primary, Maia2Opponent)
    assert opponent._ceiling == 1800


def test_stockfish_selection_is_not_routed() -> None:
    assert isinstance(
        build_opponent(Settings(_env_file=None, llm_provider="none", opponent_engine="stockfish")),
        StockfishLimitedOpponent,
    )


def test_environment_selects_the_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHESS_TUTOR_OPPONENT_ENGINE", "maia2")
    assert Settings(_env_file=None, llm_provider="none").opponent_engine == "maia2"
