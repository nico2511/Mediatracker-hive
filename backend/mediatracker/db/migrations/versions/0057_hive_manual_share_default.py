"""Hive : partage catalogue manuel par défaut, intention explicite par épisode.

Revision ID: 0057_hive_manual_share_default
Revises: 0056_download_poll_profile
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0057_hive_manual_share_default"
down_revision: str | None = "0056_download_poll_profile"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "hive_share_intent",
        sa.Column("media_item_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["media_item_id"],
            ["media_item.id"],
            name="fk_hive_share_intent_item",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("media_item_id", name="pk_hive_share_intent"),
    )

    conn = op.get_bind()
    conn.execute(
        sa.text(
            "INSERT INTO setting (key, value, is_secret, updated_at) "
            "SELECT :key, :val, 0, CURRENT_TIMESTAMP "
            "WHERE NOT EXISTS (SELECT 1 FROM setting WHERE key = :key)"
        ),
        {"key": "hive_catalog_auto_share", "val": "false"},
    )

    conn.execute(
        sa.text(
            """
            INSERT OR IGNORE INTO hive_share_intent (media_item_id)
            SELECT DISTINCT media_item_id FROM hive_entry
            WHERE etat = 'disponible' AND media_item_id IS NOT NULL
            """
        )
    )

    conn.execute(
        sa.text(
            """
            DELETE FROM hive_file_fingerprint
            WHERE sha256_hex IS NULL
            AND media_item_id NOT IN (SELECT media_item_id FROM hive_share_intent)
            """
        )
    )


def downgrade() -> None:
    op.drop_table("hive_share_intent")
    conn = op.get_bind()
    conn.execute(sa.text("DELETE FROM setting WHERE key = 'hive_catalog_auto_share'"))
