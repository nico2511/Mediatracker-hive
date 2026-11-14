"""Hive T2 : empreintes locales, exclusions, entrées signées, vecteur de versions.

Revision ID: 0049_hive_t2_catalog
Revises: 0048_hive_tables
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0049_hive_t2_catalog"
down_revision: str | None = "0048_hive_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hive_versions",
        sa.Column("ruche_id", sa.LargeBinary(length=16), nullable=False),
        sa.Column("origin_instance_id", sa.String(length=48), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(
            ["ruche_id"],
            ["hive_ruche.ruche_id"],
            name="fk_hive_versions_ruche",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("ruche_id", "origin_instance_id", name="pk_hive_versions"),
    )

    op.create_table(
        "hive_file_fingerprint",
        sa.Column("media_item_id", sa.Integer(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("mtime_ns", sa.BigInteger(), nullable=True),
        sa.Column("sha256_hex", sa.String(length=64), nullable=True),
        sa.Column("partial_sha256_hex", sa.String(length=64), nullable=True),
        sa.Column("partial_size", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["media_item_id"],
            ["media_item.id"],
            name="fk_hive_file_fingerprint_item",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("media_item_id", name="pk_hive_file_fingerprint"),
    )

    op.create_table(
        "hive_share_exclusion",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("media_item_id", sa.Integer(), nullable=False),
        # 16 octets nuls = « toutes les ruches » ; sinon identifiant de ruche (sans FK, évite les sentinelles dans hive_ruche).
        sa.Column("ruche_id", sa.LargeBinary(length=16), nullable=False),
        sa.ForeignKeyConstraint(
            ["media_item_id"],
            ["media_item.id"],
            name="fk_hive_share_exclusion_item",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hive_share_exclusion"),
        sa.UniqueConstraint("media_item_id", "ruche_id", name="uq_hive_share_exclusion_item_ruche"),
    )
    op.create_index("ix_hive_share_exclusion_item", "hive_share_exclusion", ["media_item_id"])

    op.add_column("media", sa.Column("keep_source", sa.String(length=16), nullable=True))
    op.add_column("media_item", sa.Column("keep_source", sa.String(length=16), nullable=True))

    op.add_column(
        "hive_entry",
        sa.Column("cle_json", sa.Text(), nullable=True),
    )
    op.add_column("hive_entry", sa.Column("fiche_json", sa.Text(), nullable=True))
    op.add_column("hive_entry", sa.Column("sha256_hex", sa.String(length=64), nullable=True))
    op.add_column("hive_entry", sa.Column("file_id", sa.LargeBinary(length=16), nullable=True))
    op.add_column(
        "hive_entry",
        sa.Column("etat", sa.String(length=16), nullable=False, server_default="disponible"),
    )
    op.add_column("hive_entry", sa.Column("signature_b64", sa.Text(), nullable=True))
    op.add_column("hive_entry", sa.Column("payload_json", sa.Text(), nullable=True))
    op.add_column("hive_entry", sa.Column("tombstone_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("hive_entry", sa.Column("media_item_id", sa.Integer(), nullable=True))
    op.create_index("ix_hive_entry_ruche_origin_seq", "hive_entry", ["ruche_id", "origin_instance_id", "seq"])
    op.create_index("ix_hive_entry_tombstone_at", "hive_entry", ["tombstone_at"])


def downgrade() -> None:
    op.drop_index("ix_hive_entry_tombstone_at", table_name="hive_entry")
    op.drop_index("ix_hive_entry_ruche_origin_seq", table_name="hive_entry")
    op.drop_column("hive_entry", "media_item_id")
    op.drop_column("hive_entry", "tombstone_at")
    op.drop_column("hive_entry", "payload_json")
    op.drop_column("hive_entry", "signature_b64")
    op.drop_column("hive_entry", "etat")
    op.drop_column("hive_entry", "file_id")
    op.drop_column("hive_entry", "sha256_hex")
    op.drop_column("hive_entry", "fiche_json")
    op.drop_column("hive_entry", "cle_json")
    op.drop_column("media_item", "keep_source")
    op.drop_column("media", "keep_source")
    op.drop_index("ix_hive_share_exclusion_item", table_name="hive_share_exclusion")
    op.drop_table("hive_share_exclusion")
    op.drop_table("hive_file_fingerprint")
    op.drop_table("hive_versions")
