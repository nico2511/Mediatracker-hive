"""Synopsis on cached digital releases and airing series.

Revision ID: 0018_release_overview
Revises: 0017_query_indexes
Create Date: 2026-10-13

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_release_overview"
down_revision: str | None = "0017_query_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("digital_release", sa.Column("overview", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("digital_release", "overview")
