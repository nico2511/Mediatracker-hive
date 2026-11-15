"""Hive V1a : cle_hash, compactage, invite cherche (mode ask).

Revision ID: 0072_hive_signal_refine
Revises: 0071_hive_signal
"""

from __future__ import annotations

import hashlib
import json

import sqlalchemy as sa
from alembic import op

revision: str = "0072_hive_signal_refine"
down_revision: str | None = "0071_hive_signal"
branch_labels: str | None = None
depends_on: str | None = None


def _hash_cle(text: str | None) -> str | None:
    if not text:
        return None
    try:
        cle = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(cle, dict):
        return None
    raw = json.dumps(cle, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def upgrade() -> None:
    op.add_column("hive_signal", sa.Column("cle_hash", sa.String(length=64), nullable=True))
    op.create_index(
        "ix_hive_signal_ruche_origin_cle",
        "hive_signal",
        ["ruche_id", "origin_instance_id", "cle_hash"],
    )

    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, cle_json FROM hive_signal")).fetchall()
    for row in rows:
        h = _hash_cle(row[1])
        if h:
            conn.execute(
                sa.text("UPDATE hive_signal SET cle_hash = :h WHERE id = :id"),
                {"h": h, "id": row[0]},
            )

    conn.execute(
        sa.text(
            """
            DELETE FROM hive_signal
            WHERE id NOT IN (
                SELECT MAX(id) FROM hive_signal
                GROUP BY ruche_id, origin_instance_id, cle_hash
            )
            """
        )
    )

    op.create_table(
        "hive_cherche_miss_prompt",
        sa.Column(
            "media_item_id",
            sa.Integer(),
            sa.ForeignKey("media_item.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("prompted_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("hive_cherche_miss_prompt")
    op.drop_index("ix_hive_signal_ruche_origin_cle", table_name="hive_signal")
    op.drop_column("hive_signal", "cle_hash")