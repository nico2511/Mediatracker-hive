"""Keep the release filename after a download or library rename.

Revision ID: 0016_original_name
Revises: 0015_quality_profile
Create Date: 2026-10-13

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_original_name"
down_revision: str | None = "0015_quality_profile"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("media_item", "library_file"):
        op.add_column(table, sa.Column("original_name", sa.Text(), nullable=True))
        op.add_column(table, sa.Column("file_source", sa.String(length=16), nullable=True))
        op.add_column(table, sa.Column("release_quality", sa.String(length=16), nullable=True))
        op.add_column(table, sa.Column("release_language", sa.String(length=16), nullable=True))


def downgrade() -> None:
    for table in ("library_file", "media_item"):
        op.drop_column(table, "release_language")
        op.drop_column(table, "release_quality")
        op.drop_column(table, "file_source")
        op.drop_column(table, "original_name")
