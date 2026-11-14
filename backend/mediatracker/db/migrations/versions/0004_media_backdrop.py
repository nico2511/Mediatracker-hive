"""Backdrop URL on a media fiche.

Revision ID: 0004_media_backdrop
Revises: 0003_library_file
Create Date: 2026-10-11

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_media_backdrop"
down_revision: str | None = "0003_library_file"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("media", sa.Column("backdrop_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("media", "backdrop_url")
