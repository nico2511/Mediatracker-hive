"""Hive : fiabilisation empreintes (erreur, jeton de reprise).

Revision ID: 0058_hive_fingerprint_reliability
Revises: 0057_hive_manual_share_default
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0058_hive_fingerprint_reliability"
down_revision: str | None = "0057_hive_manual_share_default"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("hive_file_fingerprint", schema=None) as batch_op:
        batch_op.add_column(sa.Column("hash_error", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("hash_claimed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("hive_file_fingerprint", schema=None) as batch_op:
        batch_op.drop_column("hash_claimed_at")
        batch_op.drop_column("hash_error")
