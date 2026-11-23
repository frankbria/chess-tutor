"""add accuracy and performance rating to game

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
    op.add_column("game", sa.Column("accuracy", sa.Float(), nullable=True))
    op.add_column("game", sa.Column("performance_rating", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("game", "performance_rating")
    op.drop_column("game", "accuracy")
