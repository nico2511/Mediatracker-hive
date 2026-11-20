"""Proposition de remplacement repli → profil principal.

Revision ID: 0082_profile_replacement_proposal
Revises: 0081_hive_catalog_epoch
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0082_profile_replacement_proposal"
down_revision: str | None = "0081_hive_catalog_epoch"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("media_item", schema=None) as batch_op:
        batch_op.add_column(sa.Column("served_poll_quality_profile_id", sa.Integer(), nullable=True))
        batch_op.create_index(
            "ix_media_item_served_poll_quality_profile_id",
            ["served_poll_quality_profile_id"],
            unique=False,
        )
        batch_op.create_foreign_key(
            "fk_media_item_served_poll_quality_profile_id",
            "quality_profile",
            ["served_poll_quality_profile_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_table(
        "profile_replacement_proposal",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("media_item_id", sa.Integer(), nullable=False),
        sa.Column("principal_profile_id", sa.Integer(), nullable=False),
        sa.Column("repli_profile_id", sa.Integer(), nullable=False),
        sa.Column("version_key", sa.String(length=256), nullable=False),
        sa.Column("link_id", sa.Integer(), nullable=True),
        sa.Column("hive_sha256_hex", sa.String(length=64), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("replace_download_id", sa.Integer(), nullable=True),
        sa.Column("old_file_path", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["link_id"], ["link.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["media_item_id"], ["media_item.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["principal_profile_id"], ["quality_profile.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["replace_download_id"], ["download.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["repli_profile_id"], ["quality_profile.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("media_item_id", "version_key", name="uq_profile_repl_item_version"),
    )
    op.create_index(
        "ix_profile_repl_item_state",
        "profile_replacement_proposal",
        ["media_item_id", "state"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_profile_repl_item_state", table_name="profile_replacement_proposal")
    op.drop_table("profile_replacement_proposal")
    with op.batch_alter_table("media_item", schema=None) as batch_op:
        batch_op.drop_constraint("fk_media_item_served_poll_quality_profile_id", type_="foreignkey")
        batch_op.drop_index("ix_media_item_served_poll_quality_profile_id")
        batch_op.drop_column("served_poll_quality_profile_id")
