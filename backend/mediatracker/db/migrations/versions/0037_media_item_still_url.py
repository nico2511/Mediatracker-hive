"""Colonne still_url sur media_item (vignette épisode TMDB).

Revision ID: 0037_media_item_still_url
Revises: 0036_catalog_entry_title_folded
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037_media_item_still_url"
down_revision: str | None = "0036_catalog_entry_title_folded"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("media_item", sa.Column("still_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("media_item", "still_url")
