"""Masque local des offres Hive refusées par la source.

Revision ID: 0060_hive_source_offer_mask
Revises: 0059_source_state_probe
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0060_hive_source_offer_mask"
down_revision: str | None = "0059_source_state_probe"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "hive_source_offer_mask",
        sa.Column("ruche_id", sa.LargeBinary(length=16), nullable=False),
        sa.Column("origin_instance_id", sa.String(length=48), nullable=False),
        sa.Column("file_id", sa.LargeBinary(length=16), nullable=False),
        sa.Column("sha256_hex", sa.String(length=64), nullable=True),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("masked_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("ruche_id", "origin_instance_id", "file_id"),
    )


def downgrade() -> None:
    op.drop_table("hive_source_offer_mask")
