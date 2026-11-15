"""Chaîne de repli propre à la fiche (fallback_profile_ids).

Revision ID: 0076_media_fallback_profile_ids
Revises: 0075_hive_signal_mini_fiche
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0076_media_fallback_profile_ids"
down_revision: str | None = "0075_hive_signal_mini_fiche"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("media", sa.Column("fallback_profile_ids", sa.Text(), nullable=True))
    conn = op.get_bind()
    row = conn.execute(sa.text("SELECT value FROM setting WHERE key = 'profile_fallback_order'")).fetchone()
    global_order = (row[0] or "").strip() if row is not None else ""
    if global_order:
        conn.execute(
            sa.text("UPDATE media SET fallback_profile_ids = :order WHERE allow_fallback = 1"),
            {"order": global_order},
        )


def downgrade() -> None:
    op.drop_column("media", "fallback_profile_ids")