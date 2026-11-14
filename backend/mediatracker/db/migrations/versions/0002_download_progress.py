"""Download progress columns for the pipeline.

Revision ID: 0002_download_progress
Revises: 0001_initial
Create Date: 2026-10-11

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_download_progress"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("download", sa.Column("done_bytes", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("download", sa.Column("speed", sa.Integer(), nullable=True))
    op.add_column("download", sa.Column("eta_seconds", sa.Integer(), nullable=True))
    op.add_column(
        "download",
        sa.Column("skipped_link_ids", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("download", "skipped_link_ids")
    op.drop_column("download", "eta_seconds")
    op.drop_column("download", "speed")
    op.drop_column("download", "done_bytes")
