"""Jellyfin library cache, watched snapshots, and manual identity pushes.

Revision ID: 0019_jellyfin_align
Revises: 0018_release_overview
Create Date: 2026-10-14

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_jellyfin_align"
down_revision: str | None = "0018_release_overview"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("media", sa.Column("jellyfin_override_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "jellyfin_library_item",
        sa.Column("jellyfin_id", sa.String(length=64), nullable=False),
        sa.Column("item_type", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("series_id", sa.String(length=64), nullable=True),
        sa.Column("parent_id", sa.String(length=64), nullable=True),
        sa.Column("tmdb_id", sa.Integer(), nullable=True),
        sa.Column("imdb_id", sa.String(length=32), nullable=True),
        sa.Column("tvdb_id", sa.Integer(), nullable=True),
        sa.Column("path", sa.Text(), nullable=True),
        sa.Column("local_path", sa.Text(), nullable=True),
        sa.Column("season", sa.Integer(), nullable=True),
        sa.Column("episode", sa.Integer(), nullable=True),
        sa.Column("played", sa.Boolean(), nullable=True),
        sa.Column("date_last_saved", sa.DateTime(timezone=True), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("jellyfin_id"),
    )
    op.create_index("ix_jellyfin_library_item_series_id", "jellyfin_library_item", ["series_id"])
    op.create_table(
        "jellyfin_watch_snapshot",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("media_item_id", sa.Integer(), nullable=True),
        sa.Column("jellyfin_item_id", sa.String(length=64), nullable=False),
        sa.Column("played", sa.Boolean(), nullable=False),
        sa.Column("path", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("restored_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_jellyfin_watch_snapshot_media_item_id", "jellyfin_watch_snapshot", ["media_item_id"])
    op.create_table(
        "jellyfin_push_job",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("media_id", sa.Integer(), nullable=True),
        sa.Column("tmdb_id", sa.Integer(), nullable=False),
        sa.Column("previous_tmdb", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("media_type", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["media_id"], ["media.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_jellyfin_push_job_media_id", "jellyfin_push_job", ["media_id"])
    op.create_index("ix_jellyfin_push_state", "jellyfin_push_job", ["state", "next_retry_at"])


def downgrade() -> None:
    op.drop_index("ix_jellyfin_push_state", table_name="jellyfin_push_job")
    op.drop_index("ix_jellyfin_push_job_media_id", table_name="jellyfin_push_job")
    op.drop_table("jellyfin_push_job")
    op.drop_index("ix_jellyfin_watch_snapshot_media_item_id", table_name="jellyfin_watch_snapshot")
    op.drop_table("jellyfin_watch_snapshot")
    op.drop_index("ix_jellyfin_library_item_series_id", table_name="jellyfin_library_item")
    op.drop_table("jellyfin_library_item")
    op.drop_column("media", "jellyfin_override_at")
