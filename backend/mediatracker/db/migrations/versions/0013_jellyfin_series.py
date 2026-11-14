"""Manual Jellyfin series id on a fiche.

Revision ID: 0013_jellyfin_series
Revises: 0012_source_order
Create Date: 2026-10-12

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_jellyfin_series"
down_revision: str | None = "0012_source_order"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("media", sa.Column("jellyfin_series_id", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("media", "jellyfin_series_id")
