"""Seasons chosen at follow time, and a one-shot backlog download.

Revision ID: 0021_tracked_seasons
Revises: 0020_identity_lww
Create Date: 2026-10-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_tracked_seasons"
down_revision: str | None = "0020_identity_lww"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("media", sa.Column("tracked_seasons", sa.JSON(), nullable=True))
    op.add_column(
        "media",
        sa.Column("download_backlog", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("media", "download_backlog")
    op.drop_column("media", "tracked_seasons")
