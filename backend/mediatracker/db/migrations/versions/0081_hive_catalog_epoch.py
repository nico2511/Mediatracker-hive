"""Hive : époque de catalogue sur ruche.

Revision ID: 0081_hive_catalog_epoch
Revises: 0080_hive_peer_info
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0081_hive_catalog_epoch"
down_revision: str | None = "0080_hive_peer_info"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("hive_ruche", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "catalog_epoch",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "catalog_reset_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("hive_ruche", schema=None) as batch_op:
        batch_op.drop_column("catalog_reset_at")
        batch_op.drop_column("catalog_epoch")
