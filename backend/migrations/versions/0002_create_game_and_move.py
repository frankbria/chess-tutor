"""create game and move tables

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "game",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("player.id"), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("player_color", sa.String(16), nullable=False),
        sa.Column("opponent_engine", sa.String(), nullable=False),
        sa.Column("opponent_elo", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("result", sa.String(16), nullable=True),
        sa.Column("pgn", sa.String(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "move",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("game_id", sa.Integer(), sa.ForeignKey("game.id"), nullable=False),
        sa.Column("ply", sa.Integer(), nullable=False),
        sa.Column("san", sa.String(), nullable=False),
        sa.Column("uci", sa.String(), nullable=False),
        sa.Column("fen_before", sa.String(), nullable=False),
        sa.UniqueConstraint("game_id", "ply"),
    )


def downgrade() -> None:
    op.drop_table("move")
    op.drop_table("game")
