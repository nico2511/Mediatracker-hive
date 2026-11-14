"""Remember which AllDebrid link a finished download may delete.

Revision ID: 0008_debrid_purge
Revises: 0007_catalog
Create Date: 2026-10-11

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_debrid_purge"
down_revision: str | None = "0007_catalog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("download", sa.Column("debrid_link", sa.Text(), nullable=True))
    op.add_column("download", sa.Column("debrid_magnet_id", sa.String(length=32), nullable=True))
    op.add_column("download", sa.Column("debrid_purged_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("download", "debrid_purged_at")
    op.drop_column("download", "debrid_magnet_id")
    op.drop_column("download", "debrid_link")
