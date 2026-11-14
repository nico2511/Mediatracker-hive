"""Hive : refus des demandes d'adhésion (rejected_at).

Revision ID: 0068_hive_join_pending_rejected
Revises: 0067_hive_lot4_source_standard
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0068_hive_join_pending_rejected"
down_revision: str | None = "0067_hive_lot4_source_standard"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "hive_join_pending",
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("hive_join_pending", "rejected_at")
