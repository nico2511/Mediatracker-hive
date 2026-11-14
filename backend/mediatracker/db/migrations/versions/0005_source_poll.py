"""Per-media source poll interval and the last check result.

Revision ID: 0005_source_poll
Revises: 0004_media_backdrop
Create Date: 2026-10-11

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_source_poll"
down_revision: str | None = "0004_media_backdrop"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("media", sa.Column("scan_interval_min", sa.Integer(), nullable=True))
    op.add_column("media", sa.Column("last_poll_result", sa.String(length=16), nullable=True))
    op.add_column("media", sa.Column("last_poll_message", sa.Text(), nullable=True))
    op.add_column(
        "media",
        sa.Column("source_error_streak", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("media", "source_error_streak")
    op.drop_column("media", "last_poll_message")
    op.drop_column("media", "last_poll_result")
    op.drop_column("media", "scan_interval_min")
