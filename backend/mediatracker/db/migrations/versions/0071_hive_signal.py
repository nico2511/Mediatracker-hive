"""Hive V1a : signaux possede/cherche, réglages demande ruche.

Revision ID: 0071_hive_signal
Revises: 0070_source_state_probe_reason
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0071_hive_signal"
down_revision: str | None = "0070_source_state_probe_reason"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "hive_signal",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ruche_id", sa.LargeBinary(length=16), nullable=False),
        sa.Column("origin_instance_id", sa.String(length=48), nullable=True),
        sa.Column("seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("signal", sa.String(length=16), nullable=False),
        sa.Column("cle_json", sa.Text(), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=True),
        sa.Column("signature_b64", sa.Text(), nullable=True),
        sa.Column("media_item_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["ruche_id"],
            ["hive_ruche.ruche_id"],
            name="fk_hive_signal_ruche",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_hive_signal_ruche_origin", "hive_signal", ["ruche_id", "origin_instance_id"])
    op.create_index(
        "ix_hive_signal_ruche_origin_seq",
        "hive_signal",
        ["ruche_id", "origin_instance_id", "seq"],
    )

    op.create_table(
        "hive_signal_versions",
        sa.Column("ruche_id", sa.LargeBinary(length=16), nullable=False),
        sa.Column("origin_instance_id", sa.String(length=48), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(
            ["ruche_id"],
            ["hive_ruche.ruche_id"],
            name="fk_hive_signal_versions_ruche",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("ruche_id", "origin_instance_id", name="pk_hive_signal_versions"),
    )

    op.create_table(
        "hive_request_resolution",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ruche_id", sa.LargeBinary(length=16), nullable=False),
        sa.Column("requester_origin", sa.String(length=48), nullable=False),
        sa.Column("cle_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("media_item_id", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["ruche_id"],
            ["hive_ruche.ruche_id"],
            name="fk_hive_request_resolution_ruche",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "ruche_id",
            "requester_origin",
            "cle_hash",
            name="uq_hive_request_resolution",
        ),
    )
    op.create_index("ix_hive_request_resolution_ruche", "hive_request_resolution", ["ruche_id"])

    op.execute(
        sa.text(
            "INSERT INTO setting (key, value, is_secret, updated_at) "
            "SELECT 'hive_request_on_miss', 'auto', 0, CURRENT_TIMESTAMP "
            "WHERE NOT EXISTS (SELECT 1 FROM setting WHERE key = 'hive_request_on_miss')"
        )
    )
    op.execute(
        sa.text(
            "INSERT INTO setting (key, value, is_secret, updated_at) "
            "SELECT 'hive_auto_accept_requests', 'false', 0, CURRENT_TIMESTAMP "
            "WHERE NOT EXISTS (SELECT 1 FROM setting WHERE key = 'hive_auto_accept_requests')"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM setting WHERE key IN ('hive_request_on_miss', 'hive_auto_accept_requests')"))
    op.drop_table("hive_request_resolution")
    op.drop_table("hive_signal_versions")
    op.drop_index("ix_hive_signal_ruche_origin_seq", table_name="hive_signal")
    op.drop_index("ix_hive_signal_ruche_origin", table_name="hive_signal")
    op.drop_table("hive_signal")