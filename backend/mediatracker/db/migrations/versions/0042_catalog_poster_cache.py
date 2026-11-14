"""Catalogue : affiche source Wawacity et cache de recherche TMDB.

Revision ID: 0042_catalog_poster_cache
Revises: 0041_unfollow_complete_series
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0042_catalog_poster_cache"
down_revision: str | None = "0041_unfollow_complete_series"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("catalog_entry", sa.Column("source_poster_url", sa.Text(), nullable=True))
    op.create_table(
        "catalog_tmdb_cache",
        sa.Column("cache_key", sa.String(length=400), primary_key=True),
        sa.Column("tmdb_id", sa.Integer(), nullable=True),
        sa.Column("poster_url", sa.Text(), nullable=True),
        sa.Column("found", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("catalog_tmdb_cache")
    op.drop_column("catalog_entry", "source_poster_url")
