"""Mtime des dossiers série/saison pour scan incrémental (F13).

Revision ID: 0033_library_folder_mtime
Revises: 0032_source_state
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033_library_folder_mtime"
down_revision: str | None = "0032_source_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "library_folder_mtime",
        sa.Column("path", sa.Text(), primary_key=True),
        sa.Column("mtime_ns", sa.BigInteger(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("library_folder_mtime")
