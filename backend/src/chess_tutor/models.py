from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

DEFAULT_RATING = 800.0
DEFAULT_RATING_DEVIATION = 350.0
DEFAULT_VOLATILITY = 0.06
DEFAULT_DISPLAY_NAME = "Player"


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
