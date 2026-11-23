"""create player and settings tables

Revision ID: 0001
Revises:
Create Date: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "player",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("rating", sa.Float(), nullable=False),
        sa.Column("rating_deviation", sa.Float(), nullable=False),
        sa.Column("volatility", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "settings",
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("player.id"), primary_key=True),
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("value", sa.String(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("settings")
    op.drop_table("player")
