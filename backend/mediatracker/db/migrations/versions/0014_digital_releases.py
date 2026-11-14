"""Cached digital releases for the Home Assistant poster row.

Revision ID: 0014_digital_releases
Revises: 0013_jellyfin_series
Create Date: 2026-10-13

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_digital_releases"
down_revision: str | None = "0013_jellyfin_series"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "digital_release",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tmdb_id", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("poster_url", sa.Text(), nullable=True),
        sa.Column("release_on", sa.Date(), nullable=False),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tmdb_id", "media_type", name="uq_digital_release_tmdb"),
    )


def downgrade() -> None:
    op.drop_table("digital_release")
