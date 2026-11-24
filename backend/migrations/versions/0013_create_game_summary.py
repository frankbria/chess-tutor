"""create game summary

Revision ID: 0013
Revises: 0011
Create Date: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "game_summary",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("game_id", sa.Integer(), sa.ForeignKey("game.id"), nullable=False, unique=True),
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("player.id"), nullable=False),
        sa.Column("summary", sa.String(), nullable=False),
        sa.Column("key_moments", sa.String(), nullable=False),
        sa.Column("concepts", sa.String(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_game_summary_player_id", "game_summary", ["player_id"])


def downgrade() -> None:
    op.drop_index("ix_game_summary_player_id", table_name="game_summary")
    op.drop_table("game_summary")
