"""Hive T4 : joignabilité pair (gossip).

Revision ID: 0052_hive_t4_gossip
Revises: 0051_hive_peer_nonce
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0052_hive_t4_gossip"
down_revision: str | None = "0051_hive_peer_nonce"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "hive_origin_reachability",
        sa.Column("ruche_id", sa.LargeBinary(length=16), nullable=False),
        sa.Column("origin_instance_id", sa.String(length=48), nullable=False),
        sa.Column("last_ok_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["ruche_id"],
            ["hive_ruche.ruche_id"],
            name="fk_hive_origin_reachability_ruche",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("ruche_id", "origin_instance_id", name="pk_hive_origin_reachability"),
    )


def downgrade() -> None:
    op.drop_table("hive_origin_reachability")
