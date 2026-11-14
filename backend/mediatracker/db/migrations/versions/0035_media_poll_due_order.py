"""Index for next_due candidate walk (ORDER BY poll schedule).

Revision ID: 0035_media_poll_due_order
Revises: 0034_library_file_integrity
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035_media_poll_due_order"
down_revision: str | None = "0034_library_file_integrity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_index(name: str) -> bool:
    bind = op.get_bind()
    row = bind.execute(
        sa.text("SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = :name"),
        {"name": name},
    ).fetchone()
    return row is not None


def upgrade() -> None:
    if _has_index("ix_media_poll_due_order"):
        return
    op.create_index(
        "ix_media_poll_due_order",
        "media",
        ["followed", "on_hold", "next_source_poll_at", "last_source_poll_at", "id"],
    )


def downgrade() -> None:
    if _has_index("ix_media_poll_due_order"):
        op.drop_index("ix_media_poll_due_order", table_name="media")
