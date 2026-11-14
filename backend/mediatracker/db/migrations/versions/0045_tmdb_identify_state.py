"""TMDB identify state and candidates on media (Jellyfin import).

Revision ID: 0045_tmdb_identify_state
Revises: 0044_film_availability_source
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0045_tmdb_identify_state"
down_revision: str | None = "0044_film_availability_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("media", sa.Column("tmdb_identify_status", sa.String(length=32), nullable=True))
    op.add_column("media", sa.Column("tmdb_identify_candidates", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("media", "tmdb_identify_candidates")
    op.drop_column("media", "tmdb_identify_status")
