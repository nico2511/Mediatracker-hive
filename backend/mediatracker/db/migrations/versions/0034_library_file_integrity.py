"""integrity_suspect on library_file (X4).

Revision ID: 0034_library_file_integrity
Revises: 0033_library_folder_mtime
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034_library_file_integrity"
down_revision: str | None = "0033_library_folder_mtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "library_file",
        sa.Column("integrity_suspect", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("library_file", "integrity_suspect")
