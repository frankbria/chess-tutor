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
