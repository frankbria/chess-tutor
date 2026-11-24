"""The coaching eval: ~30 curated positions against the real model (run: uv run pytest -m eval)."""

import json
import time
from collections.abc import Iterator
from dataclasses import asdict

import anthropic
import chess
import pytest
import pytest_asyncio
from _pytest.terminal import TerminalReporter
from eval_scoring import CaseScore, EvalCase, build_context, load_cases, score_case, summarize

from chess_tutor.analysis.move_delta import move_delta
from chess_tutor.coach.grounding import coach_reply_with_grounding
from chess_tutor.coach.llm import AnthropicLanguageModel
from chess_tutor.engines.stockfish import StockfishPool
from chess_tutor.settings import Settings

pytestmark = [pytest.mark.llm, pytest.mark.eval, pytest.mark.asyncio(loop_scope="module")]


@pytest.fixture(scope="session")
def real_settings() -> Settings:
    settings = Settings(_env_file=None, llm_provider="anthropic")
    try:
        api_key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else None
        anthropic.Anthropic(api_key=api_key).models.retrieve(settings.cheap_model)
    except (anthropic.AuthenticationError, TypeError):
        pytest.skip("no Anthropic credentials resolve (set ANTHROPIC_API_KEY or `ant auth login`)")
    return settings


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def model(real_settings: Settings) -> AnthropicLanguageModel:
    return AnthropicLanguageModel(real_settings)


@pytest.fixture(scope="module")
def results(request: pytest.FixtureRequest) -> Iterator[list[CaseScore]]:
    scores: list[CaseScore] = []
    yield scores
    if not scores:
        return
    reporter = request.config.pluginmanager.get_plugin("terminalreporter")
    assert isinstance(reporter, TerminalReporter)
    report = request.config.rootpath / "eval-report.json"
    report.write_text(json.dumps([asdict(score) for score in scores], indent=2) + "\n")
    reporter.section("coaching eval")
    reporter.line(summarize(scores))
    reporter.line(f"per-case results: {report}")


@pytest.mark.parametrize("case", load_cases(), ids=lambda case: case.id)
async def test_coaching_case(
    case: EvalCase, real_settings: Settings, model: AnthropicLanguageModel, results: list[CaseScore]
) -> None:
    board = chess.Board(case.fen)
    async with StockfishPool(real_settings.stockfish_path, 1) as pool:
        delta = await move_delta(board, board.parse_san(case.move), pool)
    context = build_context(case, delta)
    started = time.perf_counter()
    grounded = await coach_reply_with_grounding(model, context)
    score = score_case(case, context, grounded, time.perf_counter() - started)
    results.append(score)
    print(f"\n[{case.id}] {grounded.source.value}: {grounded.text} {grounded.fact_ids}")

    assert score.passes, (score, grounded.first_attempt_errors)
