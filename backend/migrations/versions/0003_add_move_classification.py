"""add move classification and evaluations

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("move") as batch:
        batch.add_column(sa.Column("classification", sa.String(), nullable=True))
        batch.add_column(sa.Column("eval_before", sa.Float(), nullable=True))
        batch.add_column(sa.Column("eval_after", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("move") as batch:
        batch.drop_column("eval_after")
        batch.drop_column("eval_before")
        batch.drop_column("classification")
