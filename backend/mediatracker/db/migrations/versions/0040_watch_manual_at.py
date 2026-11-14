"""Horodatage du dernier choix manuel vu / non vu (W-vu).

Revision ID: 0040_watch_manual_at
Revises: 0039_unfollow_obtained_films
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0040_watch_manual_at"
down_revision: str | None = "0039_unfollow_obtained_films"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("media_item", sa.Column("watch_manual_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "jellyfin_library_item",
        sa.Column("last_played_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("jellyfin_library_item", "last_played_at")
    op.drop_column("media_item", "watch_manual_at")
