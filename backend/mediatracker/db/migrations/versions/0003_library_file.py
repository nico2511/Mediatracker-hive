"""Local library files seen by the scanner.

Revision ID: 0003_library_file
Revises: 0002_download_progress
Create Date: 2026-10-11

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_library_file"
down_revision: str | None = "0002_download_progress"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "library_file",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("quality", sa.String(length=16), nullable=True),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.Column("parsed_title", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("parsed_year", sa.Integer(), nullable=True),
        sa.Column("season", sa.Integer(), nullable=True),
        sa.Column("episode", sa.Integer(), nullable=True),
        sa.Column("tmdb_id", sa.Integer(), nullable=True),
        sa.Column("media_type", sa.String(length=16), nullable=False, server_default="film"),
        sa.Column("media_id", sa.Integer(), sa.ForeignKey("media.id", ondelete="SET NULL"), nullable=True),
        sa.Column("media_item_id", sa.Integer(), sa.ForeignKey("media_item.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="unmatched"),
        sa.Column("confidence", sa.String(length=16), nullable=False, server_default="low"),
        sa.Column("candidates", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("missing_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("path", name="uq_library_file_path"),
    )
    op.create_index("ix_library_file_status", "library_file", ["status"])
    op.create_index("ix_library_file_media_id", "library_file", ["media_id"])


def downgrade() -> None:
    op.drop_index("ix_library_file_media_id", table_name="library_file")
    op.drop_index("ix_library_file_status", table_name="library_file")
    op.drop_table("library_file")
