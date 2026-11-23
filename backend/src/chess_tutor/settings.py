from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PLACEHOLDER_API_KEY = "replace-me"  # the value shipped in .env.example
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


class ClassificationThresholds(BaseModel):
    """Win-probability loss (percentage points) above which a move gets each label."""

    inaccuracy: float = 5.0
    mistake: float = 10.0
    blunder: float = 20.0
    best_loss: float = 1.0  # a move losing less than this counts as the engine's best
    great_alternative_gap: float = 15.0  # the runner-up must trail the best move by this much
    brilliant_max_loss: float = 2.0
    brilliant_min_sacrifice: int = 2  # net material (pawns) the move gives up, e.g. a minor piece
    brilliant_min_win_probability: float = 40.0  # the sacrifice must not leave the mover losing


class PhaseThresholds(BaseModel):
    opening_max_move: int = 12
    endgame_max_non_pawn_material: int = 13


class ConceptStateThresholds(BaseModel):
    recurring_after: int = 2
    focus_after: int = 4
    mastered_after_clean_games: int = 10


class RatingWeights(BaseModel):
    regular: float = 1.0
    passive_tutor: float = 0.75
    active_tutor: float = 0.5


class Thresholds(BaseModel):
    classification: ClassificationThresholds = ClassificationThresholds()
    phase: PhaseThresholds = PhaseThresholds()
    concept_state: ConceptStateThresholds = ConceptStateThresholds()
    rating_weights: RatingWeights = RatingWeights()
    tutor_stretch_offset: int = 100


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CHESS_TUTOR_",
        env_file=REPOSITORY_ROOT / ".env",
        env_nested_delimiter="__",
        extra="ignore",
    )

    backend_host: str = "127.0.0.1"
    backend_port: int = 8000
    frontend_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    database_path: Path = REPOSITORY_ROOT / "data" / "chess_tutor.db"
    stockfish_path: str = "/usr/games/stockfish"
    stockfish_pool_size: int = Field(default=2, ge=1)
    maia_command: str = "maia3-uci"
    maia_model: Literal["3m", "5m", "23m", "79m"] = "5m"
    maia_device: str = "cpu"
    maia_enabled: bool = True  # tests that never play a game switch the engine off

    llm_provider: Literal["anthropic", "none"] = "anthropic"
    llm_api_key: SecretStr | None = None
    coaching_model: str = "claude-sonnet-5-5"
    cheap_model: str = "claude-haiku-5-5"

    thresholds: Thresholds = Thresholds()

    def _has_usable_api_key(self) -> bool:
        if self.llm_api_key is None:
            return False
        return self.llm_api_key.get_secret_value().strip() not in ("", PLACEHOLDER_API_KEY)

    @model_validator(mode="after")
    def require_api_key_for_llm_provider(self) -> Self:
        if self.llm_provider != "none" and not self._has_usable_api_key():
            raise ValueError(
                "CHESS_TUTOR_LLM_API_KEY is required (set it in .env or the environment, "
                "or set CHESS_TUTOR_LLM_PROVIDER=none to run without a coach)"
            )
        return self
