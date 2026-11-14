"""Indexes for the idle worker and the queue list.

Revision ID: 0017_query_indexes
Revises: 0016_original_name
Create Date: 2026-10-13

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0017_query_indexes"
down_revision: str | None = "0016_original_name"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_download_state", "download", ["state"])
    op.create_index("ix_media_followed_poll", "media", ["followed", "next_source_poll_at"])
    op.create_index("ix_media_item_downloaded_watched", "media_item", ["downloaded", "watched"])


def downgrade() -> None:
    op.drop_index("ix_media_item_downloaded_watched", table_name="media_item")
    op.drop_index("ix_media_followed_poll", table_name="media")
    op.drop_index("ix_download_state", table_name="download")
