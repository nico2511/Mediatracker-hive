"""Hive : instantané charge pair (gossip load).

Revision ID: 0077_hive_peer_load
Revises: 0076_media_fallback_profile_ids
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0077_hive_peer_load"
down_revision: str | None = "0076_media_fallback_profile_ids"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "hive_peer_load",
        sa.Column("ruche_id", sa.LargeBinary(length=16), nullable=False),
        sa.Column("origin_instance_id", sa.String(length=48), nullable=False),
        sa.Column("out_active", sa.Integer(), nullable=False),
        sa.Column("parallel_limit", sa.Integer(), nullable=False),
        sa.Column("send_cap_bps", sa.Integer(), nullable=False),
        sa.Column("send_avail_bps", sa.Float(), nullable=True),
        sa.Column("busy", sa.Boolean(), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["ruche_id"], ["hive_ruche.ruche_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("ruche_id", "origin_instance_id"),
    )


def downgrade() -> None:
    op.drop_table("hive_peer_load")