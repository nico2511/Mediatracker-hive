"""Hive T4 : table hive_catalogue (vue fusionnée locale).

Revision ID: 0053_hive_catalogue_store
Revises: 0052_hive_t4_gossip
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0053_hive_catalogue_store"
down_revision: str | None = "0052_hive_t4_gossip"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "hive_catalogue",
        sa.Column("work_key", sa.String(length=64), nullable=False),
        sa.Column("version_key", sa.String(length=80), nullable=False),
        sa.Column("cle_json", sa.Text(), nullable=False),
        sa.Column("sources_json", sa.Text(), nullable=False),
        sa.Column("disponible", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("work_key", "version_key", name="pk_hive_catalogue"),
    )


def downgrade() -> None:
    op.drop_table("hive_catalogue")
