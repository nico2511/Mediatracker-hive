"""Hive T5 : débit mesuré par origine (dernier échange réussi).

Revision ID: 0055_hive_measured_bps
Revises: 0054_hive_t5_transfer
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0055_hive_measured_bps"
down_revision: str | None = "0054_hive_t5_transfer"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("hive_origin_reachability", sa.Column("measured_bps", sa.Float(), nullable=True))
    op.add_column("hive_origin_reachability", sa.Column("measured_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("hive_origin_reachability", "measured_at")
    op.drop_column("hive_origin_reachability", "measured_bps")
