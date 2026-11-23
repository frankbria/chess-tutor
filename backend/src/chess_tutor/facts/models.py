from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Side(StrEnum):
    WHITE = "white"
    BLACK = "black"


class FactType(StrEnum):
    MATERIAL_BALANCE = "material_balance"
    HANGING_PIECE = "hanging_piece"


class PieceRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    color: Side
    piece_type: str
    square: str


class Fact(BaseModel):
    """One verifiable statement about a position; `id` is stable for a given position."""

    model_config = ConfigDict(frozen=True)

    id: str
    type: FactType
    side: Side
    summary: str
    severity: int = 0
    squares: list[str] = Field(default_factory=list)
    pieces: list[PieceRef] = Field(default_factory=list)
    san_lines: list[list[str]] = Field(default_factory=list)


class FactPacket(BaseModel):
    model_config = ConfigDict(frozen=True)

    fen: str
    side_to_move: Side
    facts: list[Fact] = Field(default_factory=list)

    def fact(self, fact_id: str) -> Fact | None:
        return next((fact for fact in self.facts if fact.id == fact_id), None)

    def to_compact_json(self) -> str:
        return self.model_dump_json(exclude_defaults=True)
