"""Remember a manual force that may replace a file already on disk.

Revision ID: 0022_download_forced
Revises: 0021_tracked_seasons
Create Date: 2026-10-16

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_download_forced"
down_revision: str | None = "0021_tracked_seasons"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "download",
        sa.Column("forced", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("download", "forced")
