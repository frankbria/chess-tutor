"""create rating history table

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rating_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("player.id"), nullable=False),
        sa.Column("game_id", sa.Integer(), sa.ForeignKey("game.id"), nullable=False, unique=True),
        sa.Column("rating_before", sa.Float(), nullable=False),
        sa.Column("rating_after", sa.Float(), nullable=False),
        sa.Column("deviation_before", sa.Float(), nullable=False),
        sa.Column("deviation_after", sa.Float(), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
    )
    op.create_index("ix_rating_history_player_id", "rating_history", ["player_id"])


def downgrade() -> None:
    op.drop_index("ix_rating_history_player_id", table_name="rating_history")
    op.drop_table("rating_history")
