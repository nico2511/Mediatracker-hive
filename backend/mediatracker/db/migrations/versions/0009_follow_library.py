"""Catch-up flag and the library rename undo log.

Revision ID: 0009_follow_library
Revises: 0008_debrid_purge
Create Date: 2026-10-11

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_follow_library"
down_revision: str | None = "0008_debrid_purge"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "media",
        sa.Column("catch_up_pending", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "library_rename",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("moves", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("library_rename")
    op.drop_column("media", "catch_up_pending")
