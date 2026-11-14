"""Série : empreinte du dernier arrêt automatique de suivi (reprise manuelle).

Revision ID: 0062_series_auto_unfollow_key
Revises: 0061_hive_mask_origin_seq
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0062_series_auto_unfollow_key"
down_revision: str | None = "0061_hive_mask_origin_seq"
branch_labels: str | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("media", schema=None) as batch_op:
        batch_op.add_column(sa.Column("series_auto_unfollow_key", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("media", schema=None) as batch_op:
        batch_op.drop_column("series_auto_unfollow_key")
