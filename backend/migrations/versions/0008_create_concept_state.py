"""create concept state table

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "concept_state",
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("player.id"), primary_key=True),
        sa.Column("concept", sa.String(), primary_key=True),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("recency_score", sa.Float(), nullable=False),
        sa.Column("last_seen_game_id", sa.Integer(), sa.ForeignKey("game.id"), nullable=True),
        sa.Column("games_since_last_seen", sa.Integer(), nullable=False),
        sa.Column("last_processed_game_id", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("concept_state")
