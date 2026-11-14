"""Initial schema (design v0.3).

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-11

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("username", name="uq_user_username"),
    )
    op.create_table(
        "api_token",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("prefix", sa.String(length=32), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("prefix", name="uq_api_token_prefix"),
        sa.UniqueConstraint("token_hash", name="uq_api_token_hash"),
    )
    op.create_index("ix_api_token_user_id", "api_token", ["user_id"])

    op.create_table(
        "provider",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("health_status", sa.String(length=16), nullable=True),
        sa.Column("health_code", sa.String(length=64), nullable=True),
        sa.Column("health_message", sa.Text(), nullable=True),
        sa.Column("health_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("builtin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_provider_kind", "provider", ["kind"])
    op.create_index("ix_provider_type", "provider", ["type"])

    op.create_table(
        "setting",
        sa.Column("key", sa.String(length=120), primary_key=True),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("is_secret", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "media",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("tmdb_id", sa.Integer(), nullable=True),
        sa.Column("overview", sa.Text(), nullable=True),
        sa.Column("poster_url", sa.Text(), nullable=True),
        sa.Column("followed", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("auto_download", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("pref_quality", sa.String(length=16), nullable=False, server_default="HD"),
        sa.Column("pref_language", sa.String(length=16), nullable=False, server_default="VFF"),
        sa.Column("allow_fallback", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("auto_delete", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("auto_delete_days", sa.Integer(), nullable=True),
        sa.Column("keep", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("series_status", sa.String(length=32), nullable=True),
        sa.Column("on_hold", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("new_episode_policy", sa.String(length=16), nullable=False, server_default="propose"),
        sa.Column("next_episode_season", sa.Integer(), nullable=True),
        sa.Column("next_episode_number", sa.Integer(), nullable=True),
        sa.Column("next_episode_title", sa.String(length=300), nullable=True),
        sa.Column("next_air_date", sa.Date(), nullable=True),
        sa.Column("last_source_poll_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_source_poll_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_media_tmdb_id", "media", ["tmdb_id"])

    op.create_table(
        "source_binding",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("media_id", sa.Integer(), sa.ForeignKey("media.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider_id", sa.Integer(), sa.ForeignKey("provider.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("external_ref", sa.String(length=300), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("quality", sa.String(length=16), nullable=True),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.Column("season", sa.Integer(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_scanned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_source_binding_media_id", "source_binding", ["media_id"])

    op.create_table(
        "media_season",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("media_id", sa.Integer(), sa.ForeignKey("media.id", ondelete="CASCADE"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=True),
        sa.Column("overview", sa.Text(), nullable=True),
        sa.Column("air_date", sa.Date(), nullable=True),
        sa.Column("episode_count", sa.Integer(), nullable=True),
        sa.Column("tmdb_id", sa.Integer(), nullable=True),
        sa.UniqueConstraint("media_id", "number", name="uq_media_season"),
    )
    op.create_index("ix_media_season_media_id", "media_season", ["media_id"])

    op.create_table(
        "media_item",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("media_id", sa.Integer(), sa.ForeignKey("media.id", ondelete="CASCADE"), nullable=False),
        sa.Column("season", sa.Integer(), nullable=True),
        sa.Column("episode", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("air_date", sa.Date(), nullable=True),
        sa.Column("tmdb_episode_id", sa.Integer(), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="missing"),
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column("jellyfin_item_id", sa.String(length=64), nullable=True),
        sa.Column("downloaded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("downloaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("watched", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("watched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_available", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("keep", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_media_item_media_id", "media_item", ["media_id"])
    op.execute(
        "CREATE UNIQUE INDEX uq_media_item_slot "
        "ON media_item (media_id, COALESCE(season, -1), COALESCE(episode, -1))"
    )

    op.create_table(
        "link",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("media_item_id", sa.Integer(), sa.ForeignKey("media_item.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider_id", sa.Integer(), sa.ForeignKey("provider.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("hoster", sa.String(length=64), nullable=True),
        sa.Column("quality", sa.String(length=16), nullable=True),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.Column("size", sa.Integer(), nullable=True),
        sa.Column("filename", sa.String(length=500), nullable=True),
        sa.Column("external_id", sa.String(length=128), nullable=False),
        sa.Column("dead", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider_id", "external_id", name="uq_link_external"),
    )
    op.create_index("ix_link_media_item_id", "link", ["media_item_id"])

    op.create_table(
        "download",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("media_item_id", sa.Integer(), sa.ForeignKey("media_item.id", ondelete="CASCADE"), nullable=False),
        sa.Column("link_id", sa.Integer(), sa.ForeignKey("link.id", ondelete="SET NULL"), nullable=True),
        sa.Column("debrid_provider_id", sa.Integer(), sa.ForeignKey("provider.id", ondelete="SET NULL"), nullable=True),
        sa.Column("downloader_provider_id", sa.Integer(), sa.ForeignKey("provider.id", ondelete="SET NULL"), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("handle", sa.String(length=128), nullable=True),
        sa.Column("filename", sa.String(length=500), nullable=True),
        sa.Column("size", sa.Integer(), nullable=True),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_download_media_item_id", "download", ["media_item_id"])
    op.execute(
        "CREATE UNIQUE INDEX uq_download_idempotency ON download (idempotency_key) "
        "WHERE idempotency_key IS NOT NULL"
    )

    op.create_table(
        "event",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False, server_default="info"),
        sa.Column("media_id", sa.Integer(), sa.ForeignKey("media.id", ondelete="SET NULL"), nullable=True),
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("media_item.id", ondelete="SET NULL"), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_index("ix_event_ts", "event", ["ts"])
    op.create_index("ix_event_type", "event", ["type"])

    op.create_table(
        "webhook",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False, server_default="generic"),
        sa.Column("events", sa.JSON(), nullable=False),
        sa.Column("secret", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "webhook_delivery",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("webhook_id", sa.Integer(), sa.ForeignKey("webhook.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("event.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_response", sa.Text(), nullable=True),
    )
    op.create_index("ix_webhook_delivery_webhook_id", "webhook_delivery", ["webhook_id"])
    op.create_index("ix_webhook_delivery_event_id", "webhook_delivery", ["event_id"])


def downgrade() -> None:
    op.drop_table("webhook_delivery")
    op.drop_table("webhook")
    op.drop_index("ix_event_type", table_name="event")
    op.drop_index("ix_event_ts", table_name="event")
    op.drop_table("event")
    op.execute("DROP INDEX IF EXISTS uq_download_idempotency")
    op.drop_index("ix_download_media_item_id", table_name="download")
    op.drop_table("download")
    op.drop_index("ix_link_media_item_id", table_name="link")
    op.drop_table("link")
    op.execute("DROP INDEX IF EXISTS uq_media_item_slot")
    op.drop_index("ix_media_item_media_id", table_name="media_item")
    op.drop_table("media_item")
    op.drop_index("ix_media_season_media_id", table_name="media_season")
    op.drop_table("media_season")
    op.drop_index("ix_source_binding_media_id", table_name="source_binding")
    op.drop_table("source_binding")
    op.drop_index("ix_media_tmdb_id", table_name="media")
    op.drop_table("media")
    op.drop_table("setting")
    op.drop_index("ix_provider_type", table_name="provider")
    op.drop_index("ix_provider_kind", table_name="provider")
    op.drop_table("provider")
    op.drop_index("ix_api_token_user_id", table_name="api_token")
    op.drop_table("api_token")
    op.drop_table("user")
