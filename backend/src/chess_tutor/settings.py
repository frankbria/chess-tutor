from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

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
    queens_off_max_non_pawn_material: int = 26  # both sides combined; queen=9, rook=5, minor=3


class ConceptStateThresholds(BaseModel):
    recurring_after: int = 2  # games with the concept
    recurring_min_score: float = 1.3  # ...and the recency score must still be at least this
    focus_after: int = 4
    focus_min_score: float = 2.5  # the score reached by about four games in a row
    improving_after_clean_games: int = 3
    mastered_after_clean_games: int = 10
    recency_decay: float = Field(default=0.8, gt=0, lt=1)  # score multiplier per finished game


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
    opponent_rating_deviation: float = 50.0  # Maia's strength is pinned, so its RD stays low
    rating_system_constant: float = 0.5  # Glicko-2 tau


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
    maia_policy_command: str = ""  # empty: the maia3 uv tool interpreter + tools/maia3_policy
    maia_policy_startup_timeout_seconds: float = Field(default=60, gt=0)
    maia_policy_timeout_seconds: float = Field(default=10, gt=0)
    maia_model: Literal["3m", "5m", "23m", "79m"] = "5m"
    maia_device: str = "cpu"
    maia2_python: str = ""  # empty: the venv made by scripts/install_maia2.sh
    opponent_engine: Literal["maia3", "maia2", "stockfish"] = "maia3"
    opponent_stockfish_above_elo: int = 2000  # above this Elo the Stockfish-limited engine plays
    opponent_stockfish_move_seconds: float = Field(default=0.1, gt=0)
    opponent_min_think_seconds: float = Field(default=0.6, ge=0)
    opponent_max_think_seconds: float = Field(default=4.0, ge=0)
    maia_enabled: bool = True  # tests that never play a game switch the engine off

    llm_provider: Literal["anthropic", "none"] = "anthropic"
    llm_api_key: SecretStr | None = (
        None  # unset: the SDK uses ANTHROPIC_API_KEY or `ant auth login`
    )
    coaching_model: str = "claude-sonnet-5-5"
    cheap_model: str = "claude-haiku-5-5"

    thresholds: Thresholds = Thresholds()

    @model_validator(mode="after")
    def think_time_bounds_are_ordered(self) -> Self:
        if self.opponent_min_think_seconds > self.opponent_max_think_seconds:
            raise ValueError("opponent_min_think_seconds exceeds opponent_max_think_seconds")
        return self
