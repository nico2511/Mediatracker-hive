"""TMDB enrichment columns on media (lot TMDB 1).

Revision ID: 0046_tmdb_media_enrichment
Revises: 0045_tmdb_identify_state
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0046_tmdb_media_enrichment"
down_revision: str | None = "0045_tmdb_identify_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("media", sa.Column("tmdb_vote_average", sa.Float(), nullable=True))
    op.add_column("media", sa.Column("tmdb_vote_count", sa.Integer(), nullable=True))
    op.add_column("media", sa.Column("tmdb_genres", sa.JSON(), nullable=True))
    op.add_column("media", sa.Column("tmdb_runtime_min", sa.Integer(), nullable=True))
    op.add_column("media", sa.Column("tmdb_original_language", sa.String(length=16), nullable=True))
    op.add_column("media", sa.Column("tmdb_collection_id", sa.Integer(), nullable=True))
    op.add_column("media", sa.Column("tmdb_collection_name", sa.String(length=300), nullable=True))
    op.add_column("media", sa.Column("tmdb_watch_providers_fr", sa.JSON(), nullable=True))
    op.add_column("media", sa.Column("tmdb_release_by_region", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("media", "tmdb_release_by_region")
    op.drop_column("media", "tmdb_watch_providers_fr")
    op.drop_column("media", "tmdb_collection_name")
    op.drop_column("media", "tmdb_collection_id")
    op.drop_column("media", "tmdb_original_language")
    op.drop_column("media", "tmdb_runtime_min")
    op.drop_column("media", "tmdb_genres")
    op.drop_column("media", "tmdb_vote_count")
    op.drop_column("media", "tmdb_vote_average")
