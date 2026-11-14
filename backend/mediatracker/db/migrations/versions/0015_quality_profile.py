"""Named quality profiles, plus an optional per-fiche override.

Revision ID: 0015_quality_profile
Revises: 0014_digital_releases
Create Date: 2026-10-13

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_quality_profile"
down_revision: str | None = "0014_digital_releases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "quality_profile",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("languages", sa.JSON(), nullable=False),
        sa.Column("min_resolution", sa.String(length=16), nullable=True),
        sa.Column("max_resolution", sa.String(length=16), nullable=True),
        sa.Column("min_size_mb", sa.Integer(), nullable=True),
        sa.Column("max_size_mb", sa.Integer(), nullable=True),
        sa.Column("exclude_keywords", sa.JSON(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", name="uq_quality_profile_name"),
    )
    op.add_column("media", sa.Column("quality_profile_id", sa.Integer(), nullable=True))
    op.create_index("ix_media_quality_profile_id", "media", ["quality_profile_id"])
    op.execute(
        sa.text(
            "INSERT INTO quality_profile "
            "(name, languages, min_resolution, max_resolution, exclude_keywords, is_default, created_at) "
            "VALUES (:name, :languages, :min_resolution, :max_resolution, :exclude_keywords, :is_default, :created_at)"
        ).bindparams(
            name="VF/Multi 1080p",
            languages='["VFF", "VFQ", "VF", "MULTI", "VOSTFR"]',
            min_resolution="1080p",
            max_resolution="1080p",
            exclude_keywords="[]",
            is_default=1,
            created_at="2026-10-13T00:00:00+00:00",
        )
    )


def downgrade() -> None:
    op.drop_index("ix_media_quality_profile_id", table_name="media")
    op.drop_column("media", "quality_profile_id")
    op.drop_table("quality_profile")
