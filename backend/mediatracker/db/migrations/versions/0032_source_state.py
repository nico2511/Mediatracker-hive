"""Historique d'état des sources Wawacity/Comet et hébergeurs AllDebrid (H4).

Revision ID: 0032_source_state
Revises: 0031_metrics_history
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032_source_state"
down_revision: str | None = "0031_metrics_history"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_state",
        sa.Column("target_key", sa.String(length=80), primary_key=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("since", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "source_state_change",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("target_key", sa.String(length=80), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_source_state_change_changed_at", "source_state_change", ["changed_at"])
    op.create_index("ix_source_state_change_target_key", "source_state_change", ["target_key"])


def downgrade() -> None:
    op.drop_index("ix_source_state_change_target_key", table_name="source_state_change")
    op.drop_index("ix_source_state_change_changed_at", table_name="source_state_change")
    op.drop_table("source_state_change")
    op.drop_table("source_state")
