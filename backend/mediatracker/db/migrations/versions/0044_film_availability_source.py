"""Source code for computed film availability date.

Revision ID: 0044_film_availability_source
Revises: 0043_event_journal_fold
Create Date: 2026-11-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0044_film_availability_source"
down_revision: str | None = "0043_event_journal_fold"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("media", sa.Column("film_availability_source", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("media", "film_availability_source")
