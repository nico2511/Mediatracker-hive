"""Retrait volontaire demande cherche par media_item.

Revision ID: 0073_hive_cherche_retract
Revises: 0072_hive_signal_refine
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0073_hive_cherche_retract"
down_revision: str | None = "0072_hive_signal_refine"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "hive_cherche_retract",
        sa.Column(
            "media_item_id",
            sa.Integer(),
            sa.ForeignKey("media_item.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("retracted_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("hive_cherche_retract")