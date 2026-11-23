from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from chess_tutor.settings import REPOSITORY_ROOT, Settings


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("CHESS_TUTOR_LLM_API_KEY", "CHESS_TUTOR_LLM_PROVIDER", "CHESS_TUTOR_BACKEND_PORT"):
        monkeypatch.delenv(name, raising=False)


def test_missing_api_key_names_the_environment_variable() -> None:
    with pytest.raises(ValidationError, match="CHESS_TUTOR_LLM_API_KEY"):
        Settings(_env_file=None)


def test_no_llm_provider_does_not_require_a_key() -> None:
    settings = Settings(_env_file=None, llm_provider="none")

    assert settings.llm_api_key is None


def test_environment_overrides_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHESS_TUTOR_LLM_API_KEY", "secret-value")
    monkeypatch.setenv("CHESS_TUTOR_BACKEND_PORT", "9123")
    monkeypatch.setenv("CHESS_TUTOR_FRONTEND_ORIGINS", '["http://localhost:4000"]')

    settings = Settings(_env_file=None)

    assert settings.backend_port == 9123
    assert settings.frontend_origins == ["http://localhost:4000"]
    assert settings.llm_api_key is not None
    assert settings.llm_api_key.get_secret_value() == "secret-value"
    assert "secret-value" not in repr(settings)


def test_dotenv_file_is_read(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("CHESS_TUTOR_LLM_API_KEY=from-file\nCHESS_TUTOR_MAIA_DEVICE=cuda\n")

    settings = Settings(_env_file=env_file)

    assert settings.maia_device == "cuda"


def test_defaults_match_the_spec() -> None:
    settings = Settings(_env_file=None, llm_provider="none")

    assert settings.backend_port == 8000
    assert settings.stockfish_path == "/usr/games/stockfish"
    assert settings.coaching_model == "claude-sonnet-5-5"
    assert settings.cheap_model == "claude-haiku-5-5"
    assert settings.database_path == REPOSITORY_ROOT / "data" / "chess_tutor.db"
    assert settings.thresholds.rating_weights.regular == 1.0
    assert settings.thresholds.tutor_stretch_offset == 100


@pytest.mark.parametrize("blank_key", ["", "   ", "replace-me"])
def test_blank_or_placeholder_api_key_counts_as_missing(blank_key: str) -> None:
    with pytest.raises(ValidationError, match="CHESS_TUTOR_LLM_API_KEY"):
        Settings(_env_file=None, llm_api_key=SecretStr(blank_key))


def test_stockfish_pool_size_defaults_to_two_and_rejects_zero() -> None:
    assert Settings(_env_file=None, llm_provider="none").stockfish_pool_size == 2
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_provider="none", stockfish_pool_size=0)
