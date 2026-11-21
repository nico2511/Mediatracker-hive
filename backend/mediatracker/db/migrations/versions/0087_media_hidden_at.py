"""Media : date de masquage réversible (hidden_at).

Revision ID: 0087_media_hidden_at
Revises: 0086_download_source_rounds
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0087_media_hidden_at"
down_revision: str | None = "0086_download_source_rounds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "media",
        sa.Column("hidden_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("media", "hidden_at")