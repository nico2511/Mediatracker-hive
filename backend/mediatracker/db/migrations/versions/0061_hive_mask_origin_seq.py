"""Hive : seq catalogue au moment du masque 404 (levée à republication).

Revision ID: 0061_hive_mask_origin_seq
Revises: 0060_hive_source_offer_mask
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0061_hive_mask_origin_seq"
down_revision: str | None = "0060_hive_source_offer_mask"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("hive_source_offer_mask", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("masked_origin_seq", sa.Integer(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    with op.batch_alter_table("hive_source_offer_mask", schema=None) as batch_op:
        batch_op.drop_column("masked_origin_seq")
