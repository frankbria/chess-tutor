"""add game llm cost

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "game", sa.Column("llm_cost", sa.Float(), nullable=False, server_default=sa.text("0"))
    )


def downgrade() -> None:
    with op.batch_alter_table("game") as batch:
        batch.drop_column("llm_cost")
