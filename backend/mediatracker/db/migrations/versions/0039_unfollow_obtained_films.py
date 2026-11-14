"""Films déjà téléchargés : arrêter le suivi (W-vu).

Revision ID: 0039_unfollow_obtained_films
Revises: 0038_user_session_epoch
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0039_unfollow_obtained_films"
down_revision: str | None = "0038_user_session_epoch"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE media
            SET followed = 0
            WHERE type = 'film'
              AND followed = 1
              AND EXISTS (
                SELECT 1 FROM media_item mi
                WHERE mi.media_id = media.id
                  AND mi.season IS NULL
                  AND mi.episode IS NULL
                  AND (
                    mi.downloaded = 1
                    OR mi.file_path IS NOT NULL
                    OR EXISTS (
                      SELECT 1 FROM library_file lf
                      WHERE lf.media_item_id = mi.id
                        AND lf.status = 'matched'
                        AND lf.missing_at IS NULL
                    )
                  )
              )
            """
        )
    )


def downgrade() -> None:
    pass
