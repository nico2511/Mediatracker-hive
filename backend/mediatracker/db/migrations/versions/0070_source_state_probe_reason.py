"""Raison du dernier test manuel par source (réglages Sources).

Revision ID: 0070_source_state_probe_reason
Revises: 0069_drop_obsolete_settings
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0070_source_state_probe_reason"
down_revision: str | None = "0069_drop_obsolete_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("source_state", schema=None) as batch_op:
        batch_op.add_column(sa.Column("last_probe_reason", sa.String(length=500), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("source_state", schema=None) as batch_op:
        batch_op.drop_column("last_probe_reason")
