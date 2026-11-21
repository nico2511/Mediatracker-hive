"""Relational model from the v0.3 design. Behavior arrives in later milestones."""

from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from mediatracker.db.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    session_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    tokens: Mapped[list["ApiToken"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class ApiToken(Base):
    __tablename__ = "api_token"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    prefix: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    scopes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    user: Mapped[User] = relationship(back_populates="tokens")


class Provider(Base):
    __tablename__ = "provider"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    health_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    health_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    health_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    health_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Setting(Base):
    __tablename__ = "setting"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_secret: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class QualityProfile(Base):
    """Languages, resolution and size shared by Wawacity and Comet."""

    __tablename__ = "quality_profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    languages: Mapped[list] = mapped_column(JSON, nullable=False)
    min_resolution: Mapped[str | None] = mapped_column(String(16), nullable=True)
    max_resolution: Mapped[str | None] = mapped_column(String(16), nullable=True)
    min_size_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_size_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exclude_keywords: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Media(Base):
    __tablename__ = "media"
    __table_args__ = (
        Index("ix_media_followed_poll", "followed", "next_source_poll_at"),
        Index("ix_media_next_source_poll_at", "next_source_poll_at"),
        Index(
            "ix_media_poll_due_order",
            "followed",
            "on_hold",
            "next_source_poll_at",
            "last_source_poll_at",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    original_title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tmdb_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tvdb_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    imdb_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Manual Jellyfin series item id. Matching tries it before provider ids and the title.
    jellyfin_series_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Last time a person changed this fiche's TMDB id. Compared with Jellyfin DateLastSaved.
    identity_edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Values from our last successful push. A later Jellyfin read of those values is an echo.
    identity_echo_tmdb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    identity_echo_previous: Mapped[int | None] = mapped_column(Integer, nullable=True)
    identity_echo_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    overview: Mapped[str | None] = mapped_column(Text, nullable=True)
    poster_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    backdrop_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    followed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # user | identification. Identification does not follow unless the setting says so.
    follow_origin: Mapped[str] = mapped_column(String(32), nullable=False, default="user")
    auto_download: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pref_quality: Mapped[str] = mapped_column(String(16), nullable=False, default="HD")
    pref_language: Mapped[str] = mapped_column(String(16), nullable=False, default="VFF")
    allow_fallback: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # CSV d'ids de profils ; NULL = chaîne globale. Colonne historique allow_fallback ignorée par le resolver.
    fallback_profile_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Null uses the global download source order (wawacity, then Comet).
    source_order: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Null uses the global default quality profile.
    quality_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("quality_profile.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Null uses the global scan_interval_min. Minutes between source checks.
    scan_interval_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_poll_result: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_poll_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    poll_unchanged_streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_error_streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_error_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tmdb_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    auto_delete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    auto_delete_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    keep: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # manual | hive — « Garder » posé automatiquement par Hive (distinct du manuel).
    keep_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # returning | ended | canceled | planned | in_production | unknown. Null for a film.
    series_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Completion fingerprint at last automatic unfollow; blocks repeat until episodes or status change.
    series_auto_unfollow_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    on_hold: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Set when auto-download is turned back on or the fiche is resumed. The next
    # poll ignores the air window and queues missing, unwatched episodes.
    catch_up_pending: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # auto: queue a download. propose: show it, do not queue. off: ignore new episodes.
    new_episode_policy: Mapped[str] = mapped_column(String(16), nullable=False, default="propose")
    # Null means every season. A list limits the source poll to those season numbers.
    tracked_seasons: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # One poll queues what has already aired, then the flag clears. Later episodes follow the policy.
    download_backlog: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    next_episode_season: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_episode_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_episode_title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    next_air_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    film_availability_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_source_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_source_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Last Wawacity search from rediscover or ingest_other_versions (per-fiche remote search cooldown).
    last_remote_search_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_repair_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_repair_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # pending | no_token | no_results after Jellyfin import when TMDB id is missing.
    tmdb_identify_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tmdb_identify_candidates: Mapped[list | None] = mapped_column(JSON, nullable=True)
    tmdb_vote_average: Mapped[float | None] = mapped_column(Float, nullable=True)
    tmdb_vote_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tmdb_genres: Mapped[list | None] = mapped_column(JSON, nullable=True)
    tmdb_runtime_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tmdb_original_language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    tmdb_collection_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tmdb_collection_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    tmdb_watch_providers_fr: Mapped[list | None] = mapped_column(JSON, nullable=True)
    tmdb_release_by_region: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    tmdb_trailer_youtube_key: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tmdb_trailer_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    tmdb_trailer_language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    tmdb_identify_last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Versions seen on the last source check, with a rejection reason when refused.
    source_report: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    # Masquage réversible : absent des listes et de l'accueil tant que non nul.
    hidden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    items: Mapped[list["MediaItem"]] = relationship(back_populates="media", cascade="all, delete-orphan")
    seasons: Mapped[list["MediaSeason"]] = relationship(back_populates="media", cascade="all, delete-orphan")
    bindings: Mapped[list["SourceBinding"]] = relationship(back_populates="media", cascade="all, delete-orphan")


class SourceBinding(Base):
    __tablename__ = "source_binding"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    media_id: Mapped[int] = mapped_column(ForeignKey("media.id", ondelete="CASCADE"), nullable=False, index=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("provider.id", ondelete="RESTRICT"), nullable=False)
    external_ref: Mapped[str] = mapped_column(String(300), nullable=False)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    path: Mapped[str | None] = mapped_column(Text, nullable=True)
    quality: Mapped[str | None] = mapped_column(String(16), nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    season: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    path_manual: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    media: Mapped[Media] = relationship(back_populates="bindings")


class MediaSeason(Base):
    """Season row from TMDB. Episodes stay on media_item; this holds the season fiche."""

    __tablename__ = "media_season"
    __table_args__ = (UniqueConstraint("media_id", "number", name="uq_media_season"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    media_id: Mapped[int] = mapped_column(ForeignKey("media.id", ondelete="CASCADE"), nullable=False, index=True)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    overview: Mapped[str | None] = mapped_column(Text, nullable=True)
    air_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    episode_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tmdb_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    keep: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    media: Mapped[Media] = relationship(back_populates="seasons")


class MediaItem(Base):
    __tablename__ = "media_item"
    __table_args__ = (Index("ix_media_item_downloaded_watched", "downloaded", "watched"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    media_id: Mapped[int] = mapped_column(ForeignKey("media.id", ondelete="CASCADE"), nullable=False, index=True)
    season: Mapped[int | None] = mapped_column(Integer, nullable=True)
    episode: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    air_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    still_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    tmdb_episode_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="missing")
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    jellyfin_item_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Watched and downloaded are independent: a file can sit unwatched, or Jellyfin
    # can mark something watched without this app clearing the file.
    downloaded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    watched: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    watched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # auto: Jellyfin may change it. manual: a person marked it, and sync must not clear it.
    watch_source: Mapped[str] = mapped_column(String(16), nullable=False, default="auto")
    watch_manual_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    keep: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    keep_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Release filename before any Jellyfin rename. Parsing and undo keep this one.
    original_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    release_quality: Mapped[str | None] = mapped_column(String(16), nullable=True)
    release_language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Profil validé au rangement (identité ``quality_profile`` du job terminé).
    served_poll_quality_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("quality_profile.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Short audit list, e.g. voluntary trash or an unexpected file loss.
    traces: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    media: Mapped[Media] = relationship(back_populates="items")
    links: Mapped[list["Link"]] = relationship(back_populates="item", cascade="all, delete-orphan")


class ProfileReplacementProposal(Base):
    """Proposition utilisateur : remplacer un fichier repli par une version du profil principal."""

    __tablename__ = "profile_replacement_proposal"
    __table_args__ = (
        UniqueConstraint("media_item_id", "version_key", name="uq_profile_repl_item_version"),
        Index("ix_profile_repl_item_state", "media_item_id", "state"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    media_item_id: Mapped[int] = mapped_column(
        ForeignKey("media_item.id", ondelete="CASCADE"), nullable=False, index=True
    )
    principal_profile_id: Mapped[int] = mapped_column(
        ForeignKey("quality_profile.id", ondelete="RESTRICT"), nullable=False
    )
    repli_profile_id: Mapped[int] = mapped_column(ForeignKey("quality_profile.id", ondelete="RESTRICT"), nullable=False)
    version_key: Mapped[str] = mapped_column(String(256), nullable=False)
    link_id: Mapped[int | None] = mapped_column(ForeignKey("link.id", ondelete="SET NULL"), nullable=True)
    hive_sha256_hex: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # active | stale | dismissed | replacing | completed
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    replace_download_id: Mapped[int | None] = mapped_column(
        ForeignKey("download.id", ondelete="SET NULL"), nullable=True
    )
    old_file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Link(Base):
    __tablename__ = "link"
    __table_args__ = (UniqueConstraint("provider_id", "external_id", name="uq_link_external"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    media_item_id: Mapped[int] = mapped_column(
        ForeignKey("media_item.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider_id: Mapped[int] = mapped_column(ForeignKey("provider.id", ondelete="RESTRICT"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    hoster: Mapped[str | None] = mapped_column(String(64), nullable=True)
    quality: Mapped[str | None] = mapped_column(String(16), nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    filename: Mapped[str | None] = mapped_column(String(500), nullable=True)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    dead: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    dead_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dead_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    item: Mapped[MediaItem] = relationship(back_populates="links")
    provider: Mapped[Provider] = relationship()


class Download(Base):
    __tablename__ = "download"
    __table_args__ = (
        Index("ix_download_state", "state"),
        Index("ix_download_state_next_retry", "state", "next_retry_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    media_item_id: Mapped[int] = mapped_column(
        ForeignKey("media_item.id", ondelete="CASCADE"), nullable=False, index=True
    )
    link_id: Mapped[int | None] = mapped_column(ForeignKey("link.id", ondelete="SET NULL"), nullable=True)
    debrid_provider_id: Mapped[int | None] = mapped_column(
        ForeignKey("provider.id", ondelete="SET NULL"), nullable=True
    )
    downloader_provider_id: Mapped[int | None] = mapped_column(
        ForeignKey("provider.id", ondelete="SET NULL"), nullable=True
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_rounds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    handle: Mapped[str | None] = mapped_column(String(128), nullable=True)
    filename: Mapped[str | None] = mapped_column(String(500), nullable=True)
    size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    done_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    speed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    eta_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    skipped_link_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Hoster URL that AllDebrid unlocked for this job. Never a CDN URL.
    debrid_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    debrid_magnet_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    debrid_purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Hidden from the queue after « Supprimer de la liste ». The file on disk stays.
    dismissed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Manual « Forcer le téléchargement » after the user confirmed a file already on disk.
    forced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Identité stable du profil validé par le sondage (FK, pas le nom affiché).
    poll_quality_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("quality_profile.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    # When the current unlock or aria2 attempt began. A 0 % job older than this is stalled.
    attempt_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DownloadHiveFetch(Base):
    """État Hive pour un Download aria2 (grant chiffré, source figée)."""

    __tablename__ = "download_hive_fetch"
    __table_args__ = (Index("ix_download_hive_fetch_sha", "sha256_hex"),)

    download_id: Mapped[int] = mapped_column(
        ForeignKey("download.id", ondelete="CASCADE"), primary_key=True
    )
    sha256_hex: Mapped[str] = mapped_column(String(64), nullable=False)
    sources_json: Mapped[str] = mapped_column(Text, nullable=False)
    active_source_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    ruche_id: Mapped[bytes] = mapped_column(LargeBinary(16), nullable=False)
    file_id: Mapped[bytes] = mapped_column(LargeBinary(16), nullable=False)
    origin_instance_id: Mapped[str] = mapped_column(String(48), nullable=False)
    peer_base_url: Mapped[str] = mapped_column(Text, nullable=False)
    expected_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_mtime_ns: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    content_sha256_hex: Mapped[str | None] = mapped_column(String(64), nullable=True)
    grant_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    grant_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Choix explicite catalogue Hive (demande manuelle) : cohabitation multi-versions à l’import.
    requested_version_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class LibraryFile(Base):
    """One video file seen by the local library scanner."""

    __tablename__ = "library_file"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    path: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    quality: Mapped[str | None] = mapped_column(String(16), nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    parsed_title: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    parsed_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    season: Mapped[int | None] = mapped_column(Integer, nullable=True)
    episode: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tmdb_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    media_type: Mapped[str] = mapped_column(String(16), nullable=False, default="film")
    media_id: Mapped[int | None] = mapped_column(ForeignKey("media.id", ondelete="SET NULL"), nullable=True)
    media_item_id: Mapped[int | None] = mapped_column(ForeignKey("media_item.id", ondelete="SET NULL"), nullable=True)
    # matched | review | unmatched | missing
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="unmatched")
    confidence: Mapped[str] = mapped_column(String(16), nullable=False, default="low")
    candidates: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    release_quality: Mapped[str | None] = mapped_column(String(16), nullable=True)
    release_language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    missing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Set at filing time when a light MKV/MP4 check looks truncated. Cleared after a clean filing.
    integrity_suspect: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class CatalogEntry(Base):
    """One latest-release row, deduped across sources. TMDB fields stay empty until a match."""

    __tablename__ = "catalog_entry"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_catalog_entry_dedupe"),
        Index("ix_catalog_entry_type_seen", "media_type", "last_seen_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(500), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    title_folded: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    media_type: Mapped[str] = mapped_column(String(16), nullable=False)
    season: Mapped[int | None] = mapped_column(Integer, nullable=True)
    episode: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quality: Mapped[str | None] = mapped_column(String(16), nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    # Each sighting: source, url, quality, language. The first one is also the columns above.
    sources: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    tmdb_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    poster_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_poster_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    overview: Mapped[str | None] = mapped_column(Text, nullable=True)
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    genres: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    ignored: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    tmdb_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @validates("title")
    def _sync_title_folded(self, _key: str, value: str) -> str:
        from mediatracker.core.catalog import fold_title

        self.title_folded = fold_title(value)[:300]
        return value


class CatalogTmdbCache(Base):
    """TMDB search result for a catalog title (including negative hits)."""

    __tablename__ = "catalog_tmdb_cache"

    cache_key: Mapped[str] = mapped_column(String(400), primary_key=True)
    tmdb_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    poster_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    found: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DigitalRelease(Base):
    """One cached digital release (TMDB type 4) for Sorties numériques."""

    __tablename__ = "digital_release"
    __table_args__ = (UniqueConstraint("tmdb_id", "media_type", name="uq_digital_release_tmdb"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tmdb_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # film or series. Home Assistant follow maps these to movie and tv.
    media_type: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    poster_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    overview: Mapped[str | None] = mapped_column(Text, nullable=True)
    release_on: Mapped[date] = mapped_column(Date, nullable=False)
    refreshed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Event(Base):
    __tablename__ = "event"
    __table_args__ = (
        Index("ix_event_media_id", "media_id"),
        Index(
            "uq_event_dedupe",
            "type",
            "dedupe_key",
            unique=True,
            sqlite_where=text("dedupe_key IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    level: Mapped[str] = mapped_column(String(16), nullable=False, default="info")
    media_id: Mapped[int | None] = mapped_column(ForeignKey("media.id", ondelete="SET NULL"), nullable=True)
    item_id: Mapped[int | None] = mapped_column(ForeignKey("media_item.id", ondelete="SET NULL"), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    dedupe_key: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    repeat_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Webhook(Base):
    __tablename__ = "webhook"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    # generic (HMAC), ntfy (jeton optionnel), homeassistant (id dans l'URL, sans HMAC).
    type: Mapped[str] = mapped_column(String(16), nullable=False, default="generic")
    events: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class WebhookDelivery(Base):
    __tablename__ = "webhook_delivery"
    __table_args__ = (UniqueConstraint("webhook_id", "event_id", name="uq_webhook_delivery_once"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    webhook_id: Mapped[int] = mapped_column(ForeignKey("webhook.id", ondelete="CASCADE"), nullable=False, index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("event.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_response: Mapped[str | None] = mapped_column(Text, nullable=True)


class TrashEntry(Base):
    """File moved off a media root. Purge deletes the trash copy only."""

    __tablename__ = "trash_entry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    original_path: Mapped[str] = mapped_column(Text, nullable=False)
    trash_path: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    media_id: Mapped[int | None] = mapped_column(ForeignKey("media.id", ondelete="SET NULL"), nullable=True)
    media_item_id: Mapped[int | None] = mapped_column(ForeignKey("media_item.id", ondelete="SET NULL"), nullable=True)
    season: Mapped[int | None] = mapped_column(Integer, nullable=True)
    label: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    reason: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    purge_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    restored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class JellyfinLibraryItem(Base):
    """One Jellyfin library row. Alignment reads this cache instead of the server."""

    __tablename__ = "jellyfin_library_item"

    jellyfin_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    item_type: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    series_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    parent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tmdb_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    imdb_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tvdb_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    path: Mapped[str | None] = mapped_column(Text, nullable=True)
    local_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    season: Mapped[int | None] = mapped_column(Integer, nullable=True)
    episode: Mapped[int | None] = mapped_column(Integer, nullable=True)
    played: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_played_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    date_last_saved: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class JellyfinWatchSnapshot(Base):
    """Watched flag taken before a rename, restored on Jellyfin if the rescan drops it."""

    __tablename__ = "jellyfin_watch_snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    media_item_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    jellyfin_item_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    played: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    restored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class JellyfinPushJob(Base):
    """Queued push of a manual TMDB correction to Jellyfin. Retries stay idempotent."""

    __tablename__ = "jellyfin_push_job"
    __table_args__ = (Index("ix_jellyfin_push_state", "state", "next_retry_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    media_id: Mapped[int | None] = mapped_column(ForeignKey("media.id", ondelete="CASCADE"), nullable=True, index=True)
    tmdb_id: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_tmdb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    media_type: Mapped[str] = mapped_column(String(16), nullable=False, default="series")
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IdentityChange(Base):
    """One TMDB change, from MediaTracker or from Jellyfin. ``undone_at`` reverses it."""

    __tablename__ = "identity_change"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    media_id: Mapped[int] = mapped_column(ForeignKey("media.id", ondelete="CASCADE"), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    old_tmdb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_tmdb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    old_title: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    new_title: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    old_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LibraryRename(Base):
    """Undo log for a folder brought to the Jellyfin layout. ``moves`` is src → dest."""

    __tablename__ = "library_rename"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    moves: Mapped[list] = mapped_column(JSON, nullable=False, default=list)


class MetricsHistory(Base):
    """Five-minute resource and worker metric snapshots."""

    __tablename__ = "metrics_history"
    __table_args__ = (Index("ix_metrics_history_bucket_at", "bucket_at", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bucket_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cpu_api: Mapped[float | None] = mapped_column(Float, nullable=True)
    cpu_worker: Mapped[float | None] = mapped_column(Float, nullable=True)
    rss_api: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    rss_worker: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    worker_wakes: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    source_requests: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    task_durations: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class SourceState(Base):
    """Dernier état connu d'une source distante ou d'un hébergeur AllDebrid."""

    __tablename__ = "source_state"

    target_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    since: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_probe_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_probe_link_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_probe_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_probe_issue: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_probe_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LibraryFolderMtime(Base):
    """Last seen mtime for a show or season folder (incremental local scan)."""

    __tablename__ = "library_folder_mtime"

    path: Mapped[str] = mapped_column(Text, primary_key=True)
    mtime_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)


class SourceStateChange(Base):
    """Une ligne par changement d'état (ok ↔ en panne)."""

    __tablename__ = "source_state_change"
    __table_args__ = (Index("ix_source_state_change_changed_at", "changed_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_key: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class HiveRuche(Base):
    """Ruche Hive (groupe d'instances)."""

    __tablename__ = "hive_ruche"

    ruche_id: Mapped[bytes] = mapped_column(LargeBinary(16), primary_key=True)
    creator_instance_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    weekly_quota_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    catalog_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    catalog_reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    members: Mapped[list["HiveMember"]] = relationship(back_populates="ruche", cascade="all, delete-orphan")
    entries: Mapped[list["HiveEntry"]] = relationship(back_populates="ruche", cascade="all, delete-orphan")
    signals: Mapped[list["HiveSignal"]] = relationship(back_populates="ruche", cascade="all, delete-orphan")
    versions: Mapped[list["HiveVersions"]] = relationship(
        back_populates="ruche", cascade="all, delete-orphan"
    )
    signal_versions: Mapped[list["HiveSignalVersions"]] = relationship(
        back_populates="ruche", cascade="all, delete-orphan"
    )


class HiveMember(Base):
    """Membre actif d'une ruche (liste signée en vigueur)."""

    __tablename__ = "hive_member"
    __table_args__ = (UniqueConstraint("ruche_id", "member_instance_id", name="uq_hive_member_ruche_instance"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ruche_id: Mapped[bytes] = mapped_column(
        LargeBinary(16), ForeignKey("hive_ruche.ruche_id", ondelete="CASCADE"), nullable=False, index=True
    )
    member_instance_id: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    public_address: Mapped[str] = mapped_column(String(256), nullable=False, default="", server_default="")
    public_key_pem: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    name_document_json: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    name_signature_b64: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")

    ruche: Mapped["HiveRuche"] = relationship(back_populates="members")


class HiveInvitation(Base):
    """Jeton d'invitation (empreinte seule en base)."""

    __tablename__ = "hive_invitation"
    __table_args__ = (UniqueConstraint("token_hash", name="uq_hive_invitation_token_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ruche_id: Mapped[bytes] = mapped_column(
        LargeBinary(16), ForeignKey("hive_ruche.ruche_id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class HiveMemberList(Base):
    """Liste des membres versionnée, signée par le créateur."""

    __tablename__ = "hive_member_list"
    __table_args__ = (UniqueConstraint("ruche_id", "version", name="uq_hive_member_list_ruche_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ruche_id: Mapped[bytes] = mapped_column(
        LargeBinary(16), ForeignKey("hive_ruche.ruche_id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    signature_b64: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class HiveJoinPending(Base):
    """Appairage en cours (code de contrôle)."""

    __tablename__ = "hive_join_pending"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ruche_id: Mapped[bytes] = mapped_column(
        LargeBinary(16), ForeignKey("hive_ruche.ruche_id", ondelete="CASCADE"), nullable=False, index=True
    )
    invitation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("hive_invitation.id", ondelete="CASCADE"), nullable=False
    )
    invitee_instance_id: Mapped[str] = mapped_column(String(48), nullable=False)
    invitee_public_key_pem: Mapped[str] = mapped_column(Text, nullable=False)
    invitee_name_document_json: Mapped[str] = mapped_column(Text, nullable=False)
    invitee_name_signature_b64: Mapped[str] = mapped_column(Text, nullable=False)
    invitee_public_address: Mapped[str] = mapped_column(String(256), nullable=False)
    control_code: Mapped[str] = mapped_column(String(8), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class HiveRucheExcluded(Base):
    """Membres exclus ou partis : accès refusé pour la ruche."""

    __tablename__ = "hive_ruche_excluded"
    __table_args__ = (UniqueConstraint("ruche_id", "instance_id", name="uq_hive_ruche_excluded"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ruche_id: Mapped[bytes] = mapped_column(
        LargeBinary(16), ForeignKey("hive_ruche.ruche_id", ondelete="CASCADE"), nullable=False
    )
    instance_id: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    excluded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class HiveKnownIdentityName(Base):
    """Premier nom valide vu pour un identifiant (rejet des changements)."""

    __tablename__ = "hive_known_identity_name"

    instance_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    name: Mapped[str] = mapped_column(String(32), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class HiveSourceOfferMask(Base):
    """Offre catalogue masquée localement après refus source (404, mismatch)."""

    __tablename__ = "hive_source_offer_mask"

    ruche_id: Mapped[bytes] = mapped_column(LargeBinary(16), primary_key=True)
    origin_instance_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    file_id: Mapped[bytes] = mapped_column(LargeBinary(16), primary_key=True)
    sha256_hex: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    masked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    masked_origin_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class HiveCatalogue(Base):
    """Vue locale fusionnée persistée (par œuvre et version)."""

    __tablename__ = "hive_catalogue"

    work_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    version_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    cle_json: Mapped[str] = mapped_column(Text, nullable=False)
    sources_json: Mapped[str] = mapped_column(Text, nullable=False)
    disponible: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="0")
    refreshed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class HiveOriginReachability(Base):
    """Dernier contact pair réussi par origine dans une ruche (joignabilité locale)."""

    __tablename__ = "hive_origin_reachability"

    ruche_id: Mapped[bytes] = mapped_column(
        LargeBinary(16), ForeignKey("hive_ruche.ruche_id", ondelete="CASCADE"), primary_key=True
    )
    origin_instance_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    last_ok_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    measured_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    measured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class HivePeerInfo(Base):
    """Dernier instantané version / protocole reçu d'un pair (gossip)."""

    __tablename__ = "hive_peer_info"

    ruche_id: Mapped[bytes] = mapped_column(
        LargeBinary(16), ForeignKey("hive_ruche.ruche_id", ondelete="CASCADE"), primary_key=True
    )
    origin_instance_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    app_version: Mapped[str] = mapped_column(String(64), nullable=False)
    hive_protocol: Mapped[int] = mapped_column(Integer, nullable=False)
    capabilities_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class HivePeerLoad(Base):
    """Dernier instantané de charge reçu d'un pair (gossip)."""

    __tablename__ = "hive_peer_load"

    ruche_id: Mapped[bytes] = mapped_column(
        LargeBinary(16), ForeignKey("hive_ruche.ruche_id", ondelete="CASCADE"), primary_key=True
    )
    origin_instance_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    out_active: Mapped[int] = mapped_column(Integer, nullable=False)
    parallel_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    send_cap_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    send_avail_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    busy: Mapped[bool] = mapped_column(nullable=False)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class HivePeerNonce(Base):
    """Nonce vu pour anti-rejeu (fenêtre 10 min par Hive-Id)."""

    __tablename__ = "hive_peer_nonce"
    __table_args__ = (UniqueConstraint("hive_id", "nonce", name="uq_hive_peer_nonce"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hive_id: Mapped[str] = mapped_column(String(48), nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary(16), nullable=False)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)


class HiveVersions(Base):
    """Dernier seq connu par origine dans une ruche (vecteur de versions local)."""

    __tablename__ = "hive_versions"

    ruche_id: Mapped[bytes] = mapped_column(
        LargeBinary(16), ForeignKey("hive_ruche.ruche_id", ondelete="CASCADE"), primary_key=True
    )
    origin_instance_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    ruche: Mapped["HiveRuche"] = relationship(back_populates="versions")


class HiveFingerprintQueue(Base):
    """File FIFO persistante pour le processus dédié empreintes Hive."""

    __tablename__ = "hive_fingerprint_queue"
    __table_args__ = (Index("ix_hive_fingerprint_queue_status_id", "status", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    media_item_id: Mapped[int] = mapped_column(
        ForeignKey("media_item.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fail_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class HiveFileFingerprint(Base):
    """Reprise possible du hachage SHA-256 des fichiers locaux (T2)."""

    __tablename__ = "hive_file_fingerprint"

    media_item_id: Mapped[int] = mapped_column(
        ForeignKey("media_item.id", ondelete="CASCADE"), primary_key=True
    )
    path: Mapped[str] = mapped_column(Text, nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mtime_ns: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sha256_hex: Mapped[str | None] = mapped_column(String(64), nullable=True)
    partial_sha256_hex: Mapped[str | None] = mapped_column(String(64), nullable=True)
    partial_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    hash_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    hash_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    chunk_digests_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_digest_offset: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    chunk_digest_partial_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_digest_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    chunk_digests_sha256_hex: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class HiveShareIntent(Base):
    """Partage catalogue volontaire (mode manuel par défaut)."""

    __tablename__ = "hive_share_intent"

    media_item_id: Mapped[int] = mapped_column(
        ForeignKey("media_item.id", ondelete="CASCADE"), primary_key=True
    )


class HiveFetchGrant(Base):
    """Jeton d'accès fichier temporaire (hash seulement, LOT1 émetteur)."""

    __tablename__ = "hive_fetch_grant"
    __table_args__ = (
        Index("ix_hive_fetch_grant_ruche_requester_exp", "ruche_id", "requester_instance_id", "expires_at"),
        Index("ix_hive_fetch_grant_expires_at", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    grant_sha256_hex: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    ruche_id: Mapped[bytes] = mapped_column(
        LargeBinary(16), ForeignKey("hive_ruche.ruche_id", ondelete="CASCADE"), nullable=False
    )
    file_id: Mapped[bytes] = mapped_column(LargeBinary(16), nullable=False)
    requester_instance_id: Mapped[str] = mapped_column(String(48), nullable=False)
    media_item_id: Mapped[int] = mapped_column(
        ForeignKey("media_item.id", ondelete="CASCADE"), nullable=False
    )
    content_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_mtime_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_sha256_hex: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class HiveShareScope(Base):
    """Périmètre partagé (fiche entière ou une saison) pour hériter l'intention sur nouveaux fichiers."""

    __tablename__ = "hive_share_scope"
    __table_args__ = (Index("ix_hive_share_scope_media", "media_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    media_id: Mapped[int] = mapped_column(ForeignKey("media.id", ondelete="CASCADE"), nullable=False)
    season: Mapped[int | None] = mapped_column(Integer, nullable=True)


class HiveShareScopeExclusion(Base):
    """Exclusions de ruches pour un périmètre (même sémantique que ``HiveShareExclusion``)."""

    __tablename__ = "hive_share_scope_exclusion"
    __table_args__ = (
        UniqueConstraint("scope_id", "ruche_id", name="uq_hive_share_scope_exclusion_scope_ruche"),
        Index("ix_hive_share_scope_exclusion_scope", "scope_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope_id: Mapped[int] = mapped_column(ForeignKey("hive_share_scope.id", ondelete="CASCADE"), nullable=False)
    ruche_id: Mapped[bytes] = mapped_column(LargeBinary(16), nullable=False)


class HiveShareExclusion(Base):
    """Ne plus partager cet épisode : ruche précise ou 16 octets nuls (toutes les ruches)."""

    __tablename__ = "hive_share_exclusion"
    __table_args__ = (
        UniqueConstraint("media_item_id", "ruche_id", name="uq_hive_share_exclusion_item_ruche"),
        Index("ix_hive_share_exclusion_item", "media_item_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    media_item_id: Mapped[int] = mapped_column(ForeignKey("media_item.id", ondelete="CASCADE"), nullable=False)
    ruche_id: Mapped[bytes] = mapped_column(LargeBinary(16), nullable=False)


class HiveTransferJob(Base):
    """Table historique T5 (réception .part via l'ancien moteur ``HiveTransferJob``).

    Déprécié : ne plus créer ni mettre à jour de lignes — la réception Hive passe par
    ``Download`` + ``DownloadHiveFetch`` (fetch-grant / aria2). Conservé pour les données
    déjà présentes en base ; aucune migration de suppression.
    """

    __tablename__ = "hive_transfer_job"
    __table_args__ = (Index("ix_hive_transfer_job_state", "state"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="pending", server_default="pending")
    sha256_hex: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sources_json: Mapped[str] = mapped_column(Text, nullable=False)
    active_source_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    part_path: Mapped[str] = mapped_column(Text, nullable=False)
    bytes_done: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    chunk_digests_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    fiche_json: Mapped[str] = mapped_column(Text, nullable=False)
    cle_json: Mapped[str] = mapped_column(Text, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    season: Mapped[int | None] = mapped_column(Integer, nullable=True)
    episode: Mapped[int | None] = mapped_column(Integer, nullable=True)
    paused_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class HiveReceiveQuota(Base):
    """Volume reçu par membre non-partageur, par ruche et semaine."""

    __tablename__ = "hive_receive_quota"

    ruche_id: Mapped[bytes] = mapped_column(
        LargeBinary(16), ForeignKey("hive_ruche.ruche_id", ondelete="CASCADE"), primary_key=True
    )
    member_instance_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    week_start: Mapped[date] = mapped_column(Date, primary_key=True)
    bytes_used: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")


class HiveEntry(Base):
    """Entrée de catalogue signée par origine, par ruche."""

    __tablename__ = "hive_entry"
    __table_args__ = (
        Index("ix_hive_entry_ruche_origin", "ruche_id", "origin_instance_id"),
        Index("ix_hive_entry_ruche_origin_seq", "ruche_id", "origin_instance_id", "seq"),
        Index("ix_hive_entry_tombstone_at", "tombstone_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ruche_id: Mapped[bytes] = mapped_column(
        LargeBinary(16), ForeignKey("hive_ruche.ruche_id", ondelete="CASCADE"), nullable=False, index=True
    )
    origin_instance_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    cle_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    fiche_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    sha256_hex: Mapped[str | None] = mapped_column(String(64), nullable=True)
    file_id: Mapped[bytes | None] = mapped_column(LargeBinary(16), nullable=True)
    etat: Mapped[str] = mapped_column(String(16), nullable=False, default="disponible", server_default="disponible")
    signature_b64: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    tombstone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    media_item_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    ruche: Mapped["HiveRuche"] = relationship(back_populates="entries")


class HiveSignalVersions(Base):
    """Dernier seq connu par origine pour les signaux gossip (flux séparé du catalogue)."""

    __tablename__ = "hive_signal_versions"

    ruche_id: Mapped[bytes] = mapped_column(
        LargeBinary(16), ForeignKey("hive_ruche.ruche_id", ondelete="CASCADE"), primary_key=True
    )
    origin_instance_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    ruche: Mapped["HiveRuche"] = relationship(back_populates="signal_versions")


class HiveSignal(Base):
    """Signal signé possede/cherche/retire (document gossip distinct du catalogue)."""

    __tablename__ = "hive_signal"
    __table_args__ = (
        Index("ix_hive_signal_ruche_origin", "ruche_id", "origin_instance_id"),
        Index("ix_hive_signal_ruche_origin_seq", "ruche_id", "origin_instance_id", "seq"),
        Index("ix_hive_signal_ruche_origin_cle", "ruche_id", "origin_instance_id", "cle_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ruche_id: Mapped[bytes] = mapped_column(
        LargeBinary(16), ForeignKey("hive_ruche.ruche_id", ondelete="CASCADE"), nullable=False, index=True
    )
    origin_instance_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    signal: Mapped[str] = mapped_column(String(16), nullable=False)
    cle_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cle_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    signature_b64: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_item_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mini_fiche_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    ruche: Mapped["HiveRuche"] = relationship(back_populates="signals")


class HiveChercheMissPrompt(Base):
    """Recherche sources échouée en mode ask : l’UI peut proposer d’annoncer à la ruche."""

    __tablename__ = "hive_cherche_miss_prompt"

    media_item_id: Mapped[int] = mapped_column(
        ForeignKey("media_item.id", ondelete="CASCADE"), primary_key=True
    )
    prompted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class HiveChercheRetract(Base):
    """Utilisateur a retiré sa demande : pas de republication auto tant que la ligne existe."""

    __tablename__ = "hive_cherche_retract"

    media_item_id: Mapped[int] = mapped_column(
        ForeignKey("media_item.id", ondelete="CASCADE"), primary_key=True
    )
    retracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class HiveRequestResolution(Base):
    """Décision locale accepter/refuser une demande cherche (origine jamais exposée en API)."""

    __tablename__ = "hive_request_resolution"
    __table_args__ = (
        Index("ix_hive_request_resolution_ruche", "ruche_id"),
        UniqueConstraint("ruche_id", "requester_origin", "cle_hash", name="uq_hive_request_resolution"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ruche_id: Mapped[bytes] = mapped_column(
        LargeBinary(16), ForeignKey("hive_ruche.ruche_id", ondelete="CASCADE"), nullable=False
    )
    requester_origin: Mapped[str] = mapped_column(String(48), nullable=False)
    cle_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    media_item_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class HiveWantedVolunteer(Base):
    """Volontaire pour acquérir un titre listé dans Recherché (ne publie pas cherche)."""

    __tablename__ = "hive_wanted_volunteer"
    __table_args__ = (
        Index("ix_hive_wanted_volunteer_ruche", "ruche_id"),
        UniqueConstraint("ruche_id", "work_key", "volunteer_origin", name="uq_hive_wanted_volunteer"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ruche_id: Mapped[bytes] = mapped_column(
        LargeBinary(16), ForeignKey("hive_ruche.ruche_id", ondelete="CASCADE"), nullable=False
    )
    work_key: Mapped[str] = mapped_column(String(32), nullable=False)
    volunteer_origin: Mapped[str] = mapped_column(String(48), nullable=False)
    media_item_id: Mapped[int] = mapped_column(
        ForeignKey("media_item.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
