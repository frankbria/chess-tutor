from pydantic import BaseModel

from chess_tutor.models import PlayerColor


class ClockState(BaseModel):
    """Remaining milliseconds now; `running` is the side whose clock is ticking (None once over)."""

    white_ms: int
    black_ms: int
    running: PlayerColor | None
