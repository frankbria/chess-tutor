from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chess_tutor.main import create_app
from chess_tutor.settings import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        llm_provider="none",
        database_path=tmp_path / "db" / "t.db",
        maia_enabled=False,
        thought_question_every_n_moves=0,
        opponent_min_think_seconds=0,
        opponent_max_think_seconds=0,
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client
