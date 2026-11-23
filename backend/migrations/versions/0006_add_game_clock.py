"""add game clock columns

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("game", sa.Column("time_control", sa.String(16), nullable=True))
    op.add_column("game", sa.Column("white_ms", sa.Integer(), nullable=True))
    op.add_column("game", sa.Column("black_ms", sa.Integer(), nullable=True))
    op.add_column("game", sa.Column("clock_started_ms", sa.BigInteger(), nullable=True))
    op.add_column(
        "game",
        sa.Column("ended_on_time", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    with op.batch_alter_table("game") as batch:
        for column in ("ended_on_time", "clock_started_ms", "black_ms", "white_ms", "time_control"):
            batch.drop_column(column)
