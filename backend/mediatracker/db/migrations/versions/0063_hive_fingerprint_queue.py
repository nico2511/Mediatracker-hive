"""Hive : file d'attente persistante pour le calcul d'empreintes (processus dédié).

Revision ID: 0063_hive_fingerprint_queue
Revises: 0062_series_auto_unfollow_key
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0063_hive_fingerprint_queue"
down_revision: str | None = "0062_series_auto_unfollow_key"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "hive_fingerprint_queue",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "media_item_id",
            sa.Integer(),
            sa.ForeignKey("media_item.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fail_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("media_item_id", name="uq_hive_fingerprint_queue_item"),
    )
    op.create_index(
        "ix_hive_fingerprint_queue_status_id",
        "hive_fingerprint_queue",
        ["status", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_hive_fingerprint_queue_status_id", table_name="hive_fingerprint_queue")
    op.drop_table("hive_fingerprint_queue")
