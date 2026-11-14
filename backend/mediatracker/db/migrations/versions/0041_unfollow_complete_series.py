"""Séries terminées complètes : arrêter le suivi (W-statut).

Revision ID: 0041_unfollow_complete_series
Revises: 0040_watch_manual_at
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0041_unfollow_complete_series"
down_revision: str | None = "0040_watch_manual_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE media
            SET followed = 0
            WHERE type = 'series'
              AND followed = 1
              AND series_status IN ('ended', 'canceled')
              AND EXISTS (
                SELECT 1 FROM media_item mi
                WHERE mi.media_id = media.id
                  AND mi.season IS NOT NULL
                  AND mi.episode IS NOT NULL
                  AND mi.season != 0
                  AND (mi.air_date IS NULL OR mi.air_date <= date('now'))
              )
              AND NOT EXISTS (
                SELECT 1 FROM media_item mi
                WHERE mi.media_id = media.id
                  AND mi.season IS NOT NULL
                  AND mi.episode IS NOT NULL
                  AND mi.season != 0
                  AND (mi.air_date IS NULL OR mi.air_date <= date('now'))
                  AND mi.watched = 0
                  AND mi.downloaded = 0
                  AND mi.file_path IS NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM library_file lf
                    WHERE lf.media_item_id = mi.id
                      AND lf.status = 'matched'
                      AND lf.missing_at IS NULL
                  )
              )
            """
        )
    )


def downgrade() -> None:
    pass
