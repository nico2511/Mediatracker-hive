"""Manual watch, follow origin, and dismissed downloads.

Revision ID: 0010_manual_watch
Revises: 0009_follow_library
Create Date: 2026-10-11

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_manual_watch"
down_revision: str | None = "0009_follow_library"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("media", sa.Column("original_title", sa.String(length=300), nullable=True))
    op.add_column(
        "media",
        sa.Column("follow_origin", sa.String(length=32), nullable=False, server_default="user"),
    )
    op.add_column("media", sa.Column("source_report", sa.JSON(), nullable=True))
    op.add_column(
        "media_item",
        sa.Column("watch_source", sa.String(length=16), nullable=False, server_default="auto"),
    )
    op.add_column(
        "download",
        sa.Column("dismissed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("download", "dismissed")
    op.drop_column("media_item", "watch_source")
    op.drop_column("media", "source_report")
    op.drop_column("media", "follow_origin")
    op.drop_column("media", "original_title")
