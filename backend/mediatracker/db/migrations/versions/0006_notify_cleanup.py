"""Notification dedupe, season keep, and the trash table.

Revision ID: 0006_notify_cleanup
Revises: 0005_source_poll
Create Date: 2026-10-11

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_notify_cleanup"
down_revision: str | None = "0005_source_poll"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "media",
        sa.Column("source_error_epoch", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("media", sa.Column("tmdb_refreshed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "media_season",
        sa.Column("keep", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("event", sa.Column("dedupe_key", sa.String(length=200), nullable=True))
    op.create_index("ix_event_dedupe_key", "event", ["dedupe_key"])
    op.execute(
        "CREATE UNIQUE INDEX uq_event_dedupe ON event (type, dedupe_key) WHERE dedupe_key IS NOT NULL"
    )
    # resolve_fks follows webhook_delivery → … → media_item. SQLite cannot
    # reflect uq_media_item_slot (COALESCE). The index is not rebuilt here, so
    # skipping that walk keeps it and avoids the SAWarning.
    with op.batch_alter_table("webhook_delivery", reflect_kwargs={"resolve_fks": False}) as batch:
        batch.create_unique_constraint("uq_webhook_delivery_once", ["webhook_id", "event_id"])
    op.create_table(
        "trash_entry",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("original_path", sa.Text(), nullable=False),
        sa.Column("trash_path", sa.Text(), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("media_id", sa.Integer(), sa.ForeignKey("media.id", ondelete="SET NULL"), nullable=True),
        sa.Column(
            "media_item_id",
            sa.Integer(),
            sa.ForeignKey("media_item.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("season", sa.Integer(), nullable=True),
        sa.Column("label", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("reason", sa.String(length=16), nullable=False, server_default="manual"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("purge_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("restored_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("trash_path", name="uq_trash_entry_path"),
    )


def downgrade() -> None:
    op.drop_table("trash_entry")
    with op.batch_alter_table("webhook_delivery", reflect_kwargs={"resolve_fks": False}) as batch:
        batch.drop_constraint("uq_webhook_delivery_once", type_="unique")
    op.execute("DROP INDEX IF EXISTS uq_event_dedupe")
    op.drop_index("ix_event_dedupe_key", table_name="event")
    op.drop_column("event", "dedupe_key")
    op.drop_column("media_season", "keep")
    op.drop_column("media", "tmdb_refreshed_at")
    op.drop_column("media", "source_error_epoch")
