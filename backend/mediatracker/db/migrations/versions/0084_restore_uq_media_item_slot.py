"""Recreate uq_media_item_slot after 0082 batch_alter_table dropped it.

Revision ID: 0084_restore_uq_media_item_slot
Revises: 0083_hive_fetch_requested_version
"""

from __future__ import annotations

import logging

from alembic import op
from sqlalchemy import text

revision: str = "0084_restore_uq_media_item_slot"
down_revision: str | None = "0083_hive_fetch_requested_version"
branch_labels = None
depends_on = None

_LOG = logging.getLogger(__name__)
_SLOT_INDEX = "uq_media_item_slot"
_SLOT_DDL = (
    "CREATE UNIQUE INDEX uq_media_item_slot "
    "ON media_item (media_id, COALESCE(season, -1), COALESCE(episode, -1))"
)


def _index_present(connection) -> bool:
    row = connection.execute(
        text("SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = :name"),
        {"name": _SLOT_INDEX},
    ).first()
    return row is not None


def _duplicate_slots(connection) -> list[tuple]:
    return list(
        connection.execute(
            text(
                "SELECT media_id, COALESCE(season, -1), COALESCE(episode, -1), COUNT(*) AS n, "
                "GROUP_CONCAT(id) AS item_ids "
                "FROM media_item "
                "GROUP BY media_id, COALESCE(season, -1), COALESCE(episode, -1) "
                "HAVING COUNT(*) > 1"
            )
        ).all()
    )


def upgrade() -> None:
    connection = op.get_bind()
    if _index_present(connection):
        return
    duplicates = _duplicate_slots(connection)
    if duplicates:
        for media_id, season, episode, count, item_ids in duplicates:
            _LOG.warning(
                "uq_media_item_slot non recréé : doublon media_id=%s season=%s episode=%s "
                "(count=%s, media_item.id=%s). Index non créé : corrigez les doublons puis recréez "
                "l'index manuellement (la migration ne repassera pas).",
                media_id,
                season,
                episode,
                count,
                item_ids,
            )
        return
    connection.execute(text(_SLOT_DDL))
    _LOG.info("Index %s recréé.", _SLOT_INDEX)


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_SLOT_INDEX}")