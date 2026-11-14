"""TVDB and IMDB ids so Jellyfin can match a series that has no TMDB id on the episode.

Revision ID: 0011_external_ids
Revises: 0010_manual_watch
Create Date: 2026-10-12

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_external_ids"
down_revision: str | None = "0010_manual_watch"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("media", sa.Column("tvdb_id", sa.Integer(), nullable=True))
    op.add_column("media", sa.Column("imdb_id", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("media", "imdb_id")
    op.drop_column("media", "tvdb_id")
