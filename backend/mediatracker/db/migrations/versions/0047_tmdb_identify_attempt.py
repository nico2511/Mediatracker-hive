"""Horodatage des tentatives de rattrapage TMDB « à identifier ».

Revision ID: 0047_tmdb_identify_attempt
Revises: 0046_tmdb_media_enrichment
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0047_tmdb_identify_attempt"
down_revision: str | None = "0046_tmdb_media_enrichment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "media",
        sa.Column("tmdb_identify_last_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("media", "tmdb_identify_last_attempt_at")
