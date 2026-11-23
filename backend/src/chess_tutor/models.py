from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, ForeignKey, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

DEFAULT_RATING = 800.0
DEFAULT_RATING_DEVIATION = 350.0
DEFAULT_VOLATILITY = 0.06
DEFAULT_DISPLAY_NAME = "Player"


class GameMode(StrEnum):
    REGULAR = "regular"
    PASSIVE = "passive"
    ACTIVE = "active"


class PlayerColor(StrEnum):
    WHITE = "white"
    BLACK = "black"


class GameStatus(StrEnum):
    ACTIVE = "active"
    FINISHED = "finished"


class GameResult(StrEnum):
    WHITE_WINS = "1-0"
    BLACK_WINS = "0-1"
    DRAW = "1/2-1/2"


class TimeControl(StrEnum):
    """Preset clocks as base minutes + increment seconds."""

    BLITZ_5_3 = "5+3"
    RAPID_10_5 = "10+5"
    RAPID_15_10 = "15+10"

    @property
    def base_ms(self) -> int:
        return int(self.value.split("+")[0]) * 60_000

    @property
    def increment_ms(self) -> int:
        return int(self.value.split("+")[1]) * 1000


def string_enum(enum_class: type[StrEnum]) -> Enum:
    # Store the enum values ("1-0"), not the member names, as plain strings.
    return Enum(
        enum_class,
        values_callable=lambda members: [member.value for member in members],
        native_enum=False,
        length=16,
    )


class ConceptSource(StrEnum):
    MOVE = "move"
    REASONING = "reasoning"


class CoachRole(StrEnum):
    COACH = "coach"
    PLAYER = "player"


class CoachMessageKind(StrEnum):
    COMMENT = "comment"
    QUESTION = "question"
    ANSWER = "answer"
    HINT = "hint"


class CoachMessageSource(StrEnum):
    LLM = "llm"
    LLM_REGENERATED = "llm_regenerated"
    TEMPLATE = "template"


class Base(DeclarativeBase):
    pass


class Player(Base):
    __tablename__ = "player"

    id: Mapped[int] = mapped_column(primary_key=True)
    display_name: Mapped[str]
    rating: Mapped[float] = mapped_column(default=DEFAULT_RATING)
    rating_deviation: Mapped[float] = mapped_column(default=DEFAULT_RATING_DEVIATION)
    volatility: Mapped[float] = mapped_column(default=DEFAULT_VOLATILITY)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class PlayerSetting(Base):
    __tablename__ = "settings"

    player_id: Mapped[int] = mapped_column(ForeignKey("player.id"), primary_key=True)
    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[str]


class Game(Base):
    __tablename__ = "game"

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("player.id"))
    mode: Mapped[GameMode] = mapped_column(string_enum(GameMode))
    player_color: Mapped[PlayerColor] = mapped_column(string_enum(PlayerColor))
    opponent_engine: Mapped[str]
    opponent_elo: Mapped[int]
    status: Mapped[GameStatus] = mapped_column(string_enum(GameStatus))
    result: Mapped[GameResult | None] = mapped_column(string_enum(GameResult), default=None)
    pgn: Mapped[str | None] = mapped_column(default=None)
    opening_eco: Mapped[str | None] = mapped_column(default=None)
    opening_name: Mapped[str | None] = mapped_column(default=None)
    accuracy: Mapped[float | None] = mapped_column(default=None)
    performance_rating: Mapped[float | None] = mapped_column(default=None)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    time_control: Mapped[TimeControl | None] = mapped_column(string_enum(TimeControl), default=None)
    white_ms: Mapped[int | None] = mapped_column(default=None)  # remaining when clock_started_ms
    black_ms: Mapped[int | None] = mapped_column(default=None)
    clock_started_ms: Mapped[int | None] = mapped_column(default=None)  # epoch ms, side to move
    ended_on_time: Mapped[bool] = mapped_column(default=False)
    llm_cost: Mapped[float] = mapped_column(default=0.0)  # US dollars spent on the coach


class Move(Base):
    __tablename__ = "move"
    __table_args__ = (UniqueConstraint("game_id", "ply"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("game.id"))
    ply: Mapped[int]
    san: Mapped[str]
    uci: Mapped[str]
    fen_before: Mapped[str]
    classification: Mapped[str | None] = mapped_column(default=None)
    eval_before: Mapped[float | None] = mapped_column(default=None)  # mover's win probability
    eval_after: Mapped[float | None] = mapped_column(default=None)
    proposed_attempts: Mapped[int] = mapped_column(
        default=0
    )  # Active mode: rethinks before lock-in


class MoveProposal(Base):
    """Active mode's pending move for a game: not on the board until it is locked in."""

    __tablename__ = "move_proposal"

    id: Mapped[int] = mapped_column(primary_key=True)  # the analysis id handed to the client
    game_id: Mapped[int] = mapped_column(ForeignKey("game.id"), unique=True)
    ply: Mapped[int]
    uci: Mapped[str | None] = mapped_column(default=None)  # None between a rethink and the next
    rethinks: Mapped[int] = mapped_column(default=0)
    win_probability_before: Mapped[float | None] = mapped_column(default=None)
    win_probability_after: Mapped[float | None] = mapped_column(default=None)


class RatingHistory(Base):
    __tablename__ = "rating_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("player.id"))
    game_id: Mapped[int] = mapped_column(ForeignKey("game.id"), unique=True)
    rating_before: Mapped[float]
    rating_after: Mapped[float]
    deviation_before: Mapped[float]
    deviation_after: Mapped[float]
    weight: Mapped[float]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class ConceptStatus(StrEnum):
    NEW = "new"
    RECURRING = "recurring"
    FOCUS = "focus"
    IMPROVING = "improving"
    MASTERED = "mastered"


class ConceptState(Base):
    __tablename__ = "concept_state"

    player_id: Mapped[int] = mapped_column(ForeignKey("player.id"), primary_key=True)
    concept: Mapped[str] = mapped_column(primary_key=True)
    state: Mapped[ConceptStatus] = mapped_column(string_enum(ConceptStatus))
    count: Mapped[int]  # games in which the concept occurred
    recency_score: Mapped[float]
    last_seen_game_id: Mapped[int | None] = mapped_column(ForeignKey("game.id"), default=None)
    games_since_last_seen: Mapped[int]  # the current clean streak
    last_processed_game_id: Mapped[int]  # makes refreshing a finished game idempotent
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ConceptEvent(Base):
    __tablename__ = "concept_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("player.id"))
    game_id: Mapped[int] = mapped_column(ForeignKey("game.id"))
    ply: Mapped[int]
    concept: Mapped[str]
    source: Mapped[ConceptSource] = mapped_column(string_enum(ConceptSource))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class CoachMessage(Base):
    __tablename__ = "coach_message"

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("game.id"), index=True)
    ply: Mapped[int]
    role: Mapped[CoachRole] = mapped_column(string_enum(CoachRole))
    kind: Mapped[CoachMessageKind] = mapped_column(string_enum(CoachMessageKind))
    hint_level: Mapped[int | None] = mapped_column(default=None)
    text: Mapped[str]
    fact_ids: Mapped[str]  # JSON list
    source: Mapped[CoachMessageSource] = mapped_column(string_enum(CoachMessageSource))
    concepts: Mapped[str]  # JSON list of concept values
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
