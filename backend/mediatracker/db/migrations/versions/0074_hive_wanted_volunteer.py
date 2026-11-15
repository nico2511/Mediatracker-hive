"""Volontaires onglet Hive Recherché.

Revision ID: 0074_hive_wanted_volunteer
Revises: 0073_hive_cherche_retract
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0074_hive_wanted_volunteer"
down_revision: str | None = "0073_hive_cherche_retract"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "hive_wanted_volunteer",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ruche_id", sa.LargeBinary(length=16), nullable=False),
        sa.Column("work_key", sa.String(length=32), nullable=False),
        sa.Column("volunteer_origin", sa.String(length=48), nullable=False),
        sa.Column("media_item_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["media_item_id"], ["media_item.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ruche_id"], ["hive_ruche.ruche_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ruche_id", "work_key", "volunteer_origin", name="uq_hive_wanted_volunteer"),
    )
    op.create_index("ix_hive_wanted_volunteer_ruche", "hive_wanted_volunteer", ["ruche_id"])
    op.create_index(op.f("ix_hive_wanted_volunteer_media_item_id"), "hive_wanted_volunteer", ["media_item_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_hive_wanted_volunteer_media_item_id"), table_name="hive_wanted_volunteer")
    op.drop_index("ix_hive_wanted_volunteer_ruche", table_name="hive_wanted_volunteer")
    op.drop_table("hive_wanted_volunteer")