"""Colonne version catalogue explicite sur download_hive_fetch."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0083_hive_fetch_requested_version"
down_revision: str | None = "0082_profile_replacement_proposal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "download_hive_fetch",
        sa.Column("requested_version_key", sa.String(length=80), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("download_hive_fetch", "requested_version_key")
