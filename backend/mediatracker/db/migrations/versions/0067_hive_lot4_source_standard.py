"""LOT4 Hive : fetch-grant seul, archivage jobs transfert historiques.

Revision ID: 0067_hive_lot4_source_standard
Revises: 0066_download_hive_fetch
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0067_hive_lot4_source_standard"
down_revision: str | None = "0066_download_hive_fetch"
branch_labels: str | None = None
depends_on: str | None = None

_ACTIVE_JOB_STATES = ("pending", "downloading", "paused", "verifying", "verified")


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE setting SET value = 'true', updated_at = datetime('now') "
            "WHERE key = 'hive_download_via_fetch_grant'"
        )
    )
    op.execute(
        sa.text(
            "INSERT INTO setting (key, value, is_secret, updated_at) "
            "SELECT 'hive_download_via_fetch_grant', 'true', 0, datetime('now') "
            "WHERE NOT EXISTS (SELECT 1 FROM setting WHERE key = 'hive_download_via_fetch_grant')"
        )
    )
    states = ", ".join(f"'{s}'" for s in _ACTIVE_JOB_STATES)
    op.execute(
        sa.text(
            f"UPDATE hive_transfer_job SET state = 'archived', "
            f"paused_reason = 'lot4_archived', updated_at = datetime('now') "
            f"WHERE state IN ({states})"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE setting SET value = 'false', updated_at = datetime('now') "
            "WHERE key = 'hive_download_via_fetch_grant'"
        )
    )
