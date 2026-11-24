"""create move proposal

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "move", sa.Column("proposed_attempts", sa.Integer(), nullable=False, server_default="0")
    )
    op.create_table(
        "move_proposal",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("game_id", sa.Integer(), sa.ForeignKey("game.id"), nullable=False, unique=True),
        sa.Column("ply", sa.Integer(), nullable=False),
        sa.Column("san", sa.String(), nullable=False),
        sa.Column("uci", sa.String(), nullable=True),
        sa.Column("rethinks", sa.Integer(), nullable=False),
        sa.Column("win_probability_before", sa.Float(), nullable=True),
        sa.Column("win_probability_after", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("move_proposal")
    op.drop_column("move", "proposed_attempts")
