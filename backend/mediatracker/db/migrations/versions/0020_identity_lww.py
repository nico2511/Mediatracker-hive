"""Last-write-wins identity clock, push echo, and the identity journal.

Revision ID: 0020_identity_lww
Revises: 0019_jellyfin_align
Create Date: 2026-10-14

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_identity_lww"
down_revision: str | None = "0019_jellyfin_align"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("media", sa.Column("identity_edited_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("media", sa.Column("identity_echo_tmdb", sa.Integer(), nullable=True))
    op.add_column("media", sa.Column("identity_echo_previous", sa.Integer(), nullable=True))
    op.add_column("media", sa.Column("identity_echo_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        sa.text("UPDATE media SET identity_edited_at = jellyfin_override_at WHERE jellyfin_override_at IS NOT NULL")
    )
    op.drop_column("media", "jellyfin_override_at")
    op.create_table(
        "identity_change",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("media_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("old_tmdb", sa.Integer(), nullable=True),
        sa.Column("new_tmdb", sa.Integer(), nullable=True),
        sa.Column("old_title", sa.String(length=300), nullable=False),
        sa.Column("new_title", sa.String(length=300), nullable=False),
        sa.Column("old_year", sa.Integer(), nullable=True),
        sa.Column("new_year", sa.Integer(), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["media_id"], ["media.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_identity_change_media_id", "identity_change", ["media_id"])


def downgrade() -> None:
    op.drop_index("ix_identity_change_media_id", table_name="identity_change")
    op.drop_table("identity_change")
    op.add_column("media", sa.Column("jellyfin_override_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        sa.text("UPDATE media SET jellyfin_override_at = identity_edited_at WHERE identity_edited_at IS NOT NULL")
    )
    op.drop_column("media", "identity_echo_at")
    op.drop_column("media", "identity_echo_previous")
    op.drop_column("media", "identity_echo_tmdb")
    op.drop_column("media", "identity_edited_at")
