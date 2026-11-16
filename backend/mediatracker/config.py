"""Environment settings. Secrets stay in the environment or the encrypted store."""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import AliasChoices, BeforeValidator, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from mediatracker.core.public_url import coerce_public_url


def _coerce_cookie_secure(value: object) -> bool | Literal["auto"]:
    if value is True or value is False:
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "auto":
            return "auto"
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    raise ValueError("COOKIE_SECURE doit être true, false ou auto")


CookieSecureSetting = Annotated[bool | Literal["auto"], BeforeValidator(_coerce_cookie_secure)]

PublicUrlSetting = Annotated[str | None, BeforeValidator(coerce_public_url)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    secret_key: str = Field(min_length=16)
    admin_username: str = "admin"
    admin_password: str | None = None
    # Jeton exigé pour créer le premier compte via l'assistant. Vide : généré au démarrage (journaux).
    setup_token: str | None = None
    database_url: str = "sqlite:////config/app.db"
    port: int = 8080
    # DB, logs, aria2 session, backups. Separate from the media volume.
    config_dir: Path = Path("/config")
    # Downloads, media roots and .trash. One filesystem so renames stay atomic.
    data_dir: Path = Path("/data")

    alldebrid_api_key: str | None = None
    # After a file lands in the library, drop that link from the AllDebrid account.
    alldebrid_auto_purge: bool = True
    # Newly followed media use automatic download unless the request says otherwise.
    auto_download_default: bool = True
    # Identifying a library folder links TMDB. It does not follow unless this is on.
    library_auto_follow: bool = False
    # Push a manual « vu » to Jellyfin when the file is already known there.
    jellyfin_push_watched: bool = False
    # Off until a library has been checked. When on, settled folders are renamed without a click.
    jellyfin_auto_rename: bool = False
    # archive keeps finished jobs in a collapsed list. remove hides them. Files stay.
    downloads_completed_action: str = "archive"
    aria2_rpc_secret: str | None = None
    jellyfin_url: str | None = None
    jellyfin_api_key: str | None = None
    jellyfin_user_id: str | None = None
    jellyfin_webhook_secret: str | None = None
    jellyfin_path_prefix: str | None = None
    # Jellyfin prefix=MediaTracker prefix, separated by ';'. The single prefix stays a fallback.
    jellyfin_path_map: str | None = None
    jellyfin_poll_interval_min: int = 30
    tmdb_read_token: str | None = None
    # Read-only GHCR token. Optional. Used only to compare the published image.
    ghcr_read_token: str | None = None
    # Fallback when GHCR is unset: compare with the latest commit on main.
    github_update_token: str | None = None
    watchtower_url: str | None = None
    watchtower_token: str | None = None
    wawacity_base_url: str | None = None
    # Comet is a download source only. Empty disables it. Default is the public keyless instance.
    comet_base_url: str = "https://comet.feels.legal"
    # Ordered instance list (newline in DB). Seeded at migration, not from env prefill.
    comet_base_urls: str = ""
    torrentio_base_url: str = "https://torrentio.strem.fun"
    uwuplus_base_url: str = ""
    download_source_order: str = "wawacity,free_telecharger,hive,comet,torrentio,stremio_addons"
    wawacity_user_agent: str | None = None
    free_telecharger_user_agent: str | None = None
    # When true, scan redirects only store a candidate; default is automatic migration.
    wawacity_domain_propose_only: bool = False
    default_quality: str = "HD"
    default_language: str = "VFF"
    hoster_preference: str = "rapidgator,turbobit,uploady,dailyuploads,1fichier"
    max_concurrent_downloads: int = 2
    # Wawacity is polled at this interval, and only for ongoing followed series.
    scan_interval_min: int = 720
    source_request_delay_seconds: int = 5
    source_error_backoff_min: int = 180
    remote_search_cooldown_hours: int = 6
    scan_ongoing_only: bool = True
    scan_air_window_days: int = 3
    # When TMDB has no digital date, availability = theatrical + this many months.
    film_theatrical_fallback_months: int = 4
    allow_fallback: bool = False
    # Ordered quality-profile ids for global fallback (comma-separated). Empty = no fallbacks.
    profile_fallback_order: str = ""
    source_cache_seconds: int = 900
    # Latest-releases catalog. 12 h is twice a day. Retention drops rows last seen before that.
    catalog_interval_hours: int = 12
    catalog_retention_days: int = 60
    catalog_quality: str = "HD"
    catalog_language: str = "VFF"
    # Unset means DATA_DIR/media, DATA_DIR/media/Films, DATA_DIR/media/Series,
    # DATA_DIR/downloads and DATA_DIR/.trash. MOVIES_DIR overrides library_movies_path.
    media_dir: Path | None = None
    movies_dir: Path | None = None
    series_dir: Path | None = None
    downloads_dir: Path | None = None
    trash_dir: Path | None = None
    library_movies_path: str | None = None
    library_series_path: str | None = None
    library_scan_interval_hours: int = 24
    library_scan_enabled: bool = True
    auto_delete_days: int = 14
    auto_delete_enabled: bool = True
    incomplete_cleanup_enabled: bool = False
    cleanup_notice_hours: int = 24
    trash_retention_days: int = 7
    # URL publique (notifications, webhook Jellyfin affiché, CORS). PUBLIC_BASE_URL reste accepté.
    public_url: PublicUrlSetting = Field(
        default=None,
        validation_alias=AliasChoices("PUBLIC_URL", "PUBLIC_BASE_URL"),
    )
    notify_group_seconds: int = 120
    notify_quiet_start: str | None = None
    notify_quiet_end: str | None = None
    notify_timezone: str = "Europe/Paris"
    notify_on_update: bool = False
    # 0 disables the guard. Otherwise pause downloads when free space falls below this.
    disk_min_free_gb: int = 5
    # Journal alert when aria2 stays unreachable this many minutes during active polls.
    aria2_unreachable_alert_min: int = 15
    backup_keep: int = 7
    # Rotating log under CONFIG_DIR/logs.
    log_max_bytes: int = 2_000_000
    log_backup_count: int = 5
    # Local hour (notify timezone) for weekly SQLite VACUUM/ANALYZE.
    sqlite_maintenance_hour: int = 4
    # TMDB poster cache under CONFIG_DIR/cache/images (not included in SQLite backups).
    image_cache_max_bytes: int = 500 * 1024 * 1024
    image_cache_stale_days: int = 60
    # Guided Jellyfin DB import: max size per uploaded SQLite file (jellyfin.db / playback_reporting.db).
    jellyfin_history_upload_max_bytes: int = 1024 * 1024 * 1024

    # Image defaults. The NAS compose sets these; the wizard does not.
    puid: int = 1000
    pgid: int = 100
    log_level: str = "INFO"
    session_days: int = 7
    login_rate_limit: int = 5
    login_rate_window_seconds: int = 60
    # After the failure window fills, the address and the account stay locked.
    login_lockout_seconds: int = 900
    # true / false : forcer l'attribut Secure. auto : HTTPS sur la requête (X-Forwarded-Proto
    # inclus) ou PUBLIC_URL en https:// avec le même hôte que la requête.
    cookie_secure: CookieSecureSetting = "auto"
    # Adresses du proxy inverse autorisées à renseigner X-Forwarded-* (uvicorn forwarded_allow_ips).
    forwarded_allow_ips: str = "127.0.0.1"
    hive_enabled: bool = False
    hive_public_address: str | None = None
    hive_instance_name: str | None = None
    hive_name_document: str | None = None
    hive_name_signature: str | None = None
    hive_transfer_parallel: int = 2
    hive_transfer_send_bps: int = 0
    hive_transfer_recv_bps: int = 0
    hive_transfer_window_start: int = 0
    hive_transfer_window_end: int = 24 * 60
    @property
    def session_max_age(self) -> int:
        return self.session_days * 24 * 3600


def get_settings() -> Settings:
    return Settings()
