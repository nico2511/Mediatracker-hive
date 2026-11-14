"""Tables Hive v1 (ruches, membres, entrées par ruche).

Revision ID: 0048_hive_tables
Revises: 0047_tmdb_identify_attempt
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0048_hive_tables"
down_revision: str | None = "0047_tmdb_identify_attempt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hive_ruche",
        sa.Column("ruche_id", sa.LargeBinary(length=16), nullable=False),
        sa.Column("creator_instance_id", sa.String(length=48), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("ruche_id", name="pk_hive_ruche"),
    )
    op.create_table(
        "hive_member",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("ruche_id", sa.LargeBinary(length=16), nullable=False),
        sa.Column("member_instance_id", sa.String(length=48), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["ruche_id"],
            ["hive_ruche.ruche_id"],
            name="fk_hive_member_ruche",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hive_member"),
        sa.UniqueConstraint("ruche_id", "member_instance_id", name="uq_hive_member_ruche_instance"),
    )
    op.create_index("ix_hive_member_instance", "hive_member", ["member_instance_id"])
    op.create_table(
        "hive_entry",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("ruche_id", sa.LargeBinary(length=16), nullable=False),
        sa.Column("origin_instance_id", sa.String(length=48), nullable=True),
        sa.Column("seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["ruche_id"],
            ["hive_ruche.ruche_id"],
            name="fk_hive_entry_ruche",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hive_entry"),
    )
    op.create_index("ix_hive_entry_ruche_origin", "hive_entry", ["ruche_id", "origin_instance_id"])


def downgrade() -> None:
    op.drop_index("ix_hive_entry_ruche_origin", table_name="hive_entry")
    op.drop_table("hive_entry")
    op.drop_index("ix_hive_member_instance", table_name="hive_member")
    op.drop_table("hive_member")
    op.drop_table("hive_ruche")
