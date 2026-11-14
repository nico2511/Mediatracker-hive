"""Dernier test et dernier succès par source (réglages Sources).

Revision ID: 0059_source_state_probe
Revises: 0058_hive_fingerprint_reliability
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0059_source_state_probe"
down_revision: str | None = "0058_hive_fingerprint_reliability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("source_state", schema=None) as batch_op:
        batch_op.add_column(sa.Column("last_probe_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("last_probe_link_count", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("last_probe_duration_ms", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("last_probe_issue", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("source_state", schema=None) as batch_op:
        batch_op.drop_column("last_success_at")
        batch_op.drop_column("last_probe_issue")
        batch_op.drop_column("last_probe_duration_ms")
        batch_op.drop_column("last_probe_link_count")
        batch_op.drop_column("last_probe_at")
