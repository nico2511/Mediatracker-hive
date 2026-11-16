"""TMDB bande-annonce (clé YouTube + métadonnées sous-titre).

Revision ID: 0078_media_tmdb_trailer
Revises: 0077_hive_peer_load
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0078_media_tmdb_trailer"
down_revision: str | None = "0077_hive_peer_load"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("media", sa.Column("tmdb_trailer_youtube_key", sa.String(length=32), nullable=True))
    op.add_column("media", sa.Column("tmdb_trailer_kind", sa.String(length=16), nullable=True))
    op.add_column("media", sa.Column("tmdb_trailer_language", sa.String(length=8), nullable=True))


def downgrade() -> None:
    op.drop_column("media", "tmdb_trailer_language")
    op.drop_column("media", "tmdb_trailer_kind")
    op.drop_column("media", "tmdb_trailer_youtube_key")