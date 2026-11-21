"""Réglage hive_auto_take_wanted (stockage uniquement, pas d’effet runtime ici).

Revision ID: 0085_hive_auto_take_wanted
Revises: 0084_restore_uq_media_item_slot
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0085_hive_auto_take_wanted"
down_revision: str | None = "0084_restore_uq_media_item_slot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "INSERT INTO setting (key, value, is_secret, updated_at) "
            "SELECT 'hive_auto_take_wanted', 'false', 0, CURRENT_TIMESTAMP "
            "WHERE NOT EXISTS (SELECT 1 FROM setting WHERE key = 'hive_auto_take_wanted')"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM setting WHERE key = 'hive_auto_take_wanted'"))