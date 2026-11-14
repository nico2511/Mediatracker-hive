"""Per-fiche throttle for rediscover and version fallback searches.

Revision ID: 0026_remote_search_at
Revises: 0025_media_item_traces
Create Date: 2026-10-22

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_remote_search_at"
down_revision: str | None = "0025_media_item_traces"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "media",
        sa.Column("last_remote_search_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("media", "last_remote_search_at")
