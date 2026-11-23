"""create coach message

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "coach_message",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("game_id", sa.Integer(), sa.ForeignKey("game.id"), nullable=False),
        sa.Column("ply", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("hint_level", sa.Integer(), nullable=True),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("fact_ids", sa.String(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("concepts", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_coach_message_game_id", "coach_message", ["game_id"])


def downgrade() -> None:
    op.drop_index("ix_coach_message_game_id", table_name="coach_message")
    op.drop_table("coach_message")
