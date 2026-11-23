import pytest
from fastapi.testclient import TestClient

from chess_tutor.main import create_app
from chess_tutor.settings import Settings


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(Settings(_env_file=None, llm_provider="none")))
