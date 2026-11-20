"""Hive : instantané version / protocole pair (gossip instance).

Revision ID: 0080_hive_peer_info
Revises: 0079_hive_share_scope
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0080_hive_peer_info"
down_revision: str | None = "0079_hive_share_scope"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "hive_peer_info",
        sa.Column("ruche_id", sa.LargeBinary(length=16), nullable=False),
        sa.Column("origin_instance_id", sa.String(length=48), nullable=False),
        sa.Column("app_version", sa.String(length=64), nullable=False),
        sa.Column("hive_protocol", sa.Integer(), nullable=False),
        sa.Column("capabilities_json", sa.JSON(), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["ruche_id"], ["hive_ruche.ruche_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("ruche_id", "origin_instance_id"),
    )


def downgrade() -> None:
    op.drop_table("hive_peer_info")
