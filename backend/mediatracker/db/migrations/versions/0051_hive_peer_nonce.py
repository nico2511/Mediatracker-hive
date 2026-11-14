"""Nonces anti-rejeu des requêtes pair Hive.

Revision ID: 0051_hive_peer_nonce
Revises: 0050_hive_t3_ruches
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0051_hive_peer_nonce"
down_revision: str | None = "0050_hive_t3_ruches"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hive_peer_nonce",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("hive_id", sa.String(length=48), nullable=False),
        sa.Column("nonce", sa.LargeBinary(length=16), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_hive_peer_nonce"),
        sa.UniqueConstraint("hive_id", "nonce", name="uq_hive_peer_nonce"),
    )
    op.create_index("ix_hive_peer_nonce_seen_at", "hive_peer_nonce", ["seen_at"])


def downgrade() -> None:
    op.drop_index("ix_hive_peer_nonce_seen_at", table_name="hive_peer_nonce")
    op.drop_table("hive_peer_nonce")
