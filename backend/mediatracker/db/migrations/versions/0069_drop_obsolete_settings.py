"""Supprime les réglages obsolètes (notify Hive, flaresolverr, flag fetch-grant).

Revision ID: 0069_drop_obsolete_settings
Revises: 0068_hive_join_pending_rejected
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0069_drop_obsolete_settings"
down_revision: str | None = "0068_hive_join_pending_rejected"
branch_labels: str | None = None
depends_on: str | None = None

_OBSOLETE_KEYS = (
    "hive_transfer_notify",
    "flaresolverr_url",
    "hive_download_via_fetch_grant",
)


def upgrade() -> None:
    for key in _OBSOLETE_KEYS:
        op.execute(sa.text("DELETE FROM setting WHERE key = :key").bindparams(key=key))


def downgrade() -> None:
    pass
