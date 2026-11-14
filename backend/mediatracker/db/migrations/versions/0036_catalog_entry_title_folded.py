"""Colonne title_folded pour les filtres catalogue en SQL.

Revision ID: 0036_catalog_entry_title_folded
Revises: 0035_media_poll_due_order
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0036_catalog_entry_title_folded"
down_revision: str | None = "0035_media_poll_due_order"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "catalog_entry",
        sa.Column("title_folded", sa.String(length=300), nullable=False, server_default=""),
    )
    bind = op.get_bind()
    from mediatracker.core.catalog import fold_title

    rows = bind.execute(sa.text("SELECT id, title FROM catalog_entry")).fetchall()
    for row in rows:
        folded = fold_title(row.title)[:300]
        bind.execute(
            sa.text("UPDATE catalog_entry SET title_folded = :folded WHERE id = :id"),
            {"folded": folded, "id": row.id},
        )


def downgrade() -> None:
    op.drop_column("catalog_entry", "title_folded")
