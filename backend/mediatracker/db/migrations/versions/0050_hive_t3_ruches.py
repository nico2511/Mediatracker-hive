"""Hive T3 : invitations, listes de membres, exclusions.

Revision ID: 0050_hive_t3_ruches
Revises: 0049_hive_t2_catalog
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0050_hive_t3_ruches"
down_revision: str | None = "0049_hive_t2_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("hive_member", sa.Column("public_address", sa.String(length=256), nullable=False, server_default=""))
    op.add_column("hive_member", sa.Column("public_key_pem", sa.Text(), nullable=False, server_default=""))
    op.add_column("hive_member", sa.Column("name_document_json", sa.Text(), nullable=False, server_default=""))
    op.add_column("hive_member", sa.Column("name_signature_b64", sa.Text(), nullable=False, server_default=""))

    op.create_table(
        "hive_invitation",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("ruche_id", sa.LargeBinary(length=16), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["ruche_id"],
            ["hive_ruche.ruche_id"],
            name="fk_hive_invitation_ruche",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hive_invitation"),
    )
    op.create_index("ix_hive_invitation_ruche", "hive_invitation", ["ruche_id"])
    op.create_index("ix_hive_invitation_token_hash", "hive_invitation", ["token_hash"], unique=True)

    op.create_table(
        "hive_member_list",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("ruche_id", sa.LargeBinary(length=16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("signature_b64", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["ruche_id"],
            ["hive_ruche.ruche_id"],
            name="fk_hive_member_list_ruche",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hive_member_list"),
        sa.UniqueConstraint("ruche_id", "version", name="uq_hive_member_list_ruche_version"),
    )
    op.create_index("ix_hive_member_list_ruche", "hive_member_list", ["ruche_id"])

    op.create_table(
        "hive_join_pending",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("ruche_id", sa.LargeBinary(length=16), nullable=False),
        sa.Column("invitation_id", sa.Integer(), nullable=False),
        sa.Column("invitee_instance_id", sa.String(length=48), nullable=False),
        sa.Column("invitee_public_key_pem", sa.Text(), nullable=False),
        sa.Column("invitee_name_document_json", sa.Text(), nullable=False),
        sa.Column("invitee_name_signature_b64", sa.Text(), nullable=False),
        sa.Column("invitee_public_address", sa.String(length=256), nullable=False),
        sa.Column("control_code", sa.String(length=8), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["invitation_id"],
            ["hive_invitation.id"],
            name="fk_hive_join_pending_invitation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ruche_id"],
            ["hive_ruche.ruche_id"],
            name="fk_hive_join_pending_ruche",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hive_join_pending"),
    )
    op.create_index("ix_hive_join_pending_ruche", "hive_join_pending", ["ruche_id"])

    op.create_table(
        "hive_ruche_excluded",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("ruche_id", sa.LargeBinary(length=16), nullable=False),
        sa.Column("instance_id", sa.String(length=48), nullable=False),
        sa.Column("excluded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["ruche_id"],
            ["hive_ruche.ruche_id"],
            name="fk_hive_ruche_excluded_ruche",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hive_ruche_excluded"),
        sa.UniqueConstraint("ruche_id", "instance_id", name="uq_hive_ruche_excluded"),
    )
    op.create_index("ix_hive_ruche_excluded_instance", "hive_ruche_excluded", ["instance_id"])

    op.create_table(
        "hive_known_identity_name",
        sa.Column("instance_id", sa.String(length=48), nullable=False),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("instance_id", name="pk_hive_known_identity_name"),
    )


def downgrade() -> None:
    op.drop_table("hive_known_identity_name")
    op.drop_index("ix_hive_ruche_excluded_instance", table_name="hive_ruche_excluded")
    op.drop_table("hive_ruche_excluded")
    op.drop_index("ix_hive_join_pending_ruche", table_name="hive_join_pending")
    op.drop_table("hive_join_pending")
    op.drop_index("ix_hive_member_list_ruche", table_name="hive_member_list")
    op.drop_table("hive_member_list")
    op.drop_index("ix_hive_invitation_token_hash", table_name="hive_invitation")
    op.drop_index("ix_hive_invitation_ruche", table_name="hive_invitation")
    op.drop_table("hive_invitation")
    op.drop_column("hive_member", "name_signature_b64")
    op.drop_column("hive_member", "name_document_json")
    op.drop_column("hive_member", "public_key_pem")
    op.drop_column("hive_member", "public_address")
