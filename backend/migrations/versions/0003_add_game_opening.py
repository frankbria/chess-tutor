"""add opening ECO and name to game

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
    op.add_column("game", sa.Column("opening_eco", sa.String(), nullable=True))
    op.add_column("game", sa.Column("opening_name", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("game", "opening_name")
    op.drop_column("game", "opening_eco")
