"""Key/value settings. Secrets are encrypted at rest and masked in API responses.

Environment values are copied on first boot only. Later edits live in the database.
"""

import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.profile_fallback import (
    PROFILE_FALLBACK_ORDER_KEY,
    format_profile_fallback_order,
    parse_profile_fallback_order,
    validate_profile_fallback_ids,
)
from mediatracker.core.selection import (
    DEFAULT_DOWNLOAD_SOURCE_ORDER,
    DOWNLOAD_SOURCE_ORDER_WITH_UWUPLUS,
    LANGUAGES,
    LEGACY_DOWNLOAD_SOURCE_ORDER,
    QUALITIES,
    parse_choice_list,
    parse_download_source_order,
    parse_hoster_preference,
    registered_download_sources,
)
from mediatracker.db.models import Setting
from mediatracker.hive.crypto_id import validate_instance_name
from mediatracker.hive.identity import (
    HIVE_ENABLED_KEY,
    HIVE_INSTANCE_NAME_KEY,
    HIVE_ONLY_KEY,
    HIVE_SETTING_KEYS,
    RESERVED_HIVE_KEYS,
    TRANSIENT_ACTIVATION_NAME_KEY,
    hive_identity_is_lost,
    parse_bool_setting,
    prepare_hive_settings_update,
)
from mediatracker.security.crypto import decrypt_secret, encrypt_secret
from mediatracker.security.ssrf import OutboundUrlRejected, settings_rejection_message, validate_outbound_url_static

PROVIDERS_SEEDED_KEY = "system.providers_seeded"

_INT_BOUNDS: dict[str, tuple[int, int]] = {
    "max_concurrent_downloads": (1, 8),
    "scan_interval_min": (60, 10080),
    "source_request_delay_seconds": (2, 60),
    "source_error_backoff_min": (30, 1440),
    "remote_search_cooldown_hours": (1, 72),
    "alldebrid_magnet_wait_max_seconds": (30, 86400),
    "alldebrid_magnet_poll_seconds": (5, 600),
    "scan_air_window_days": (0, 30),
    "film_theatrical_fallback_months": (0, 24),
    "source_cache_seconds": (0, 3600),
    "catalog_interval_hours": (1, 168),
    "catalog_retention_days": (1, 3650),
    "library_scan_interval_hours": (1, 168),
    "jellyfin_poll_interval_min": (5, 1440),
    "auto_delete_days": (1, 365),
    "trash_retention_days": (1, 90),
    "notify_group_seconds": (0, 3600),
    "cleanup_notice_hours": (1, 168),
    "disk_min_free_gb": (0, 1024),
    "aria2_unreachable_alert_min": (1, 1440),
    "backup_keep": (1, 90),
    "hive_transfer_parallel": (1, 16),
    "hive_transfer_send_bps": (0, 2_000_000_000),
    "hive_transfer_recv_bps": (0, 2_000_000_000),
    "hive_transfer_window_start": (0, 24 * 60 - 1),
    "hive_transfer_window_end": (1, 24 * 60),
    "hive_catalog_full_sync_interval_sec": (60, 7 * 24 * 60 * 60),
}

_CLOCK = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


@dataclass(frozen=True)
class SettingSpec:
    secret: bool
    kind: Literal["str", "int", "bool"]
    attr: str
    generate: bool = False
    optional: bool = False


_OPTIONAL_UNSEEDED = frozenset({"auto_delete_enabled"})

SETTING_SPECS: dict[str, SettingSpec] = {
    "default_quality": SettingSpec(False, "str", "default_quality"),
    "default_language": SettingSpec(False, "str", "default_language"),
    "hoster_preference": SettingSpec(False, "str", "hoster_preference"),
    "max_concurrent_downloads": SettingSpec(False, "int", "max_concurrent_downloads"),
    "scan_interval_min": SettingSpec(False, "int", "scan_interval_min"),
    "source_request_delay_seconds": SettingSpec(False, "int", "source_request_delay_seconds"),
    "source_error_backoff_min": SettingSpec(False, "int", "source_error_backoff_min"),
    "remote_search_cooldown_hours": SettingSpec(False, "int", "remote_search_cooldown_hours"),
    "alldebrid_magnet_wait_max_seconds": SettingSpec(False, "int", "alldebrid_magnet_wait_max_seconds"),
    "alldebrid_magnet_poll_seconds": SettingSpec(False, "int", "alldebrid_magnet_poll_seconds"),
    "scan_episode_sources_fallback": SettingSpec(False, "bool", "scan_episode_sources_fallback"),
    "scan_ongoing_only": SettingSpec(False, "bool", "scan_ongoing_only"),
    "scan_air_window_days": SettingSpec(False, "int", "scan_air_window_days"),
    "film_theatrical_fallback_months": SettingSpec(False, "int", "film_theatrical_fallback_months"),
    "film_trigger_us_digital": SettingSpec(False, "bool", "film_trigger_us_digital"),
    "allow_fallback": SettingSpec(False, "bool", "allow_fallback"),
    "profile_fallback_order": SettingSpec(False, "str", "profile_fallback_order", optional=True),
    "source_cache_seconds": SettingSpec(False, "int", "source_cache_seconds"),
    "catalog_interval_hours": SettingSpec(False, "int", "catalog_interval_hours"),
    "catalog_retention_days": SettingSpec(False, "int", "catalog_retention_days"),
    "catalog_quality": SettingSpec(False, "str", "catalog_quality", optional=True),
    "catalog_language": SettingSpec(False, "str", "catalog_language", optional=True),
    "tmdb_display_language": SettingSpec(False, "str", "tmdb_display_language", optional=True),
    "tmdb_region": SettingSpec(False, "str", "tmdb_region", optional=True),
    "tmdb_original_languages": SettingSpec(False, "str", "tmdb_original_languages", optional=True),
    "media_dir": SettingSpec(False, "str", "media_dir", optional=True),
    "library_movies_path": SettingSpec(False, "str", "library_movies_path", optional=True),
    "library_series_path": SettingSpec(False, "str", "library_series_path", optional=True),
    "downloads_dir": SettingSpec(False, "str", "downloads_dir", optional=True),
    "trash_dir": SettingSpec(False, "str", "trash_dir", optional=True),
    "library_scan_interval_hours": SettingSpec(False, "int", "library_scan_interval_hours"),
    "library_scan_enabled": SettingSpec(False, "bool", "library_scan_enabled"),
    "auto_delete_days": SettingSpec(False, "int", "auto_delete_days"),
    "auto_delete_enabled": SettingSpec(False, "bool", "auto_delete_enabled"),
    "incomplete_cleanup_enabled": SettingSpec(False, "bool", "incomplete_cleanup_enabled"),
    "cleanup_notice_hours": SettingSpec(False, "int", "cleanup_notice_hours"),
    "trash_retention_days": SettingSpec(False, "int", "trash_retention_days"),
    "public_base_url": SettingSpec(False, "str", "public_url", optional=True),
    "notify_group_seconds": SettingSpec(False, "int", "notify_group_seconds"),
    "notify_quiet_start": SettingSpec(False, "str", "notify_quiet_start", optional=True),
    "notify_quiet_end": SettingSpec(False, "str", "notify_quiet_end", optional=True),
    "notify_timezone": SettingSpec(False, "str", "notify_timezone"),
    "notify_on_update": SettingSpec(False, "bool", "notify_on_update"),
    "disk_min_free_gb": SettingSpec(False, "int", "disk_min_free_gb"),
    "aria2_unreachable_alert_min": SettingSpec(False, "int", "aria2_unreachable_alert_min"),
    "backup_keep": SettingSpec(False, "int", "backup_keep"),
    "aria2_rpc_secret": SettingSpec(True, "str", "aria2_rpc_secret", generate=True),
    "jellyfin_webhook_secret": SettingSpec(True, "str", "jellyfin_webhook_secret", generate=True),
    "jellyfin_url": SettingSpec(False, "str", "jellyfin_url", optional=True),
    "jellyfin_api_key": SettingSpec(True, "str", "jellyfin_api_key", optional=True),
    "jellyfin_user_id": SettingSpec(False, "str", "jellyfin_user_id", optional=True),
    "jellyfin_path_prefix": SettingSpec(False, "str", "jellyfin_path_prefix", optional=True),
    "jellyfin_path_map": SettingSpec(False, "str", "jellyfin_path_map", optional=True),
    "jellyfin_poll_interval_min": SettingSpec(False, "int", "jellyfin_poll_interval_min"),
    "tmdb_read_token": SettingSpec(True, "str", "tmdb_read_token", optional=True),
    "ghcr_read_token": SettingSpec(True, "str", "ghcr_read_token", optional=True),
    "github_update_token": SettingSpec(True, "str", "github_update_token", optional=True),
    "watchtower_url": SettingSpec(False, "str", "watchtower_url", optional=True),
    "watchtower_token": SettingSpec(True, "str", "watchtower_token", optional=True),
    "wawacity_base_url": SettingSpec(False, "str", "wawacity_base_url", optional=True),
    "comet_base_url": SettingSpec(False, "str", "comet_base_url", optional=True),
    "comet_base_urls": SettingSpec(False, "str", "comet_base_urls", optional=True),
    "torrentio_base_url": SettingSpec(False, "str", "torrentio_base_url", optional=True),
    "uwuplus_base_url": SettingSpec(False, "str", "uwuplus_base_url", optional=True),
    "stremio_addons": SettingSpec(False, "str", "stremio_addons", optional=True),
    "download_source_order": SettingSpec(False, "str", "download_source_order"),
    "wawacity_user_agent": SettingSpec(False, "str", "wawacity_user_agent", optional=True),
    "free_telecharger_user_agent": SettingSpec(False, "str", "free_telecharger_user_agent", optional=True),
    "wawacity_domain_propose_only": SettingSpec(False, "bool", "wawacity_domain_propose_only"),
    "alldebrid_auto_purge": SettingSpec(False, "bool", "alldebrid_auto_purge"),
    "auto_download_default": SettingSpec(False, "bool", "auto_download_default"),
    "library_auto_follow": SettingSpec(False, "bool", "library_auto_follow"),
    "jellyfin_push_watched": SettingSpec(False, "bool", "jellyfin_push_watched"),
    "jellyfin_auto_rename": SettingSpec(False, "bool", "jellyfin_auto_rename"),
    "downloads_completed_action": SettingSpec(False, "str", "downloads_completed_action"),
    "hive_enabled": SettingSpec(False, "bool", "hive_enabled", optional=True),
    "hive_public_address": SettingSpec(False, "str", "hive_public_address", optional=True),
    "hive_instance_name": SettingSpec(False, "str", "hive_instance_name", optional=True),
    "hive_name_document": SettingSpec(False, "str", "hive_name_document", optional=True),
    "hive_name_signature": SettingSpec(False, "str", "hive_name_signature", optional=True),
    "hive_transfer_parallel": SettingSpec(False, "int", "hive_transfer_parallel"),
    "hive_transfer_send_bps": SettingSpec(False, "int", "hive_transfer_send_bps"),
    "hive_transfer_recv_bps": SettingSpec(False, "int", "hive_transfer_recv_bps"),
    "hive_transfer_window_start": SettingSpec(False, "int", "hive_transfer_window_start"),
    "hive_transfer_window_end": SettingSpec(False, "int", "hive_transfer_window_end"),
    "hive_catalog_auto_share": SettingSpec(False, "bool", "hive_catalog_auto_share", optional=True),
    "hive_catalog_full_sync_interval_sec": SettingSpec(
        False, "int", "hive_catalog_full_sync_interval_sec", optional=True
    ),
    "hive_request_on_miss": SettingSpec(False, "str", "hive_request_on_miss", optional=True),
    "hive_auto_accept_requests": SettingSpec(False, "bool", "hive_auto_accept_requests", optional=True),
    "hive_auto_take_wanted": SettingSpec(False, "bool", "hive_auto_take_wanted", optional=True),
    "hive_only": SettingSpec(False, "bool", "hive_only", optional=True),
}


class SettingsError(ValueError):
    pass


def _setting_row_text(db: Session, key: str) -> str:
    row = db.get(Setting, key)
    return (row.value or "").strip() if row is not None else ""


def _visible_setting_specs() -> dict[str, SettingSpec]:
    return {key: spec for key, spec in SETTING_SPECS.items() if key not in RESERVED_HIVE_KEYS}


def migrate_download_source_order_setting(db: Session) -> None:
    """Normalize saved order: legacy defaults and retired ``uwuplus`` entries. Idempotent."""
    row = db.get(Setting, "download_source_order")
    if row is None or not (row.value or "").strip():
        return
    allowed = tuple(registered_download_sources()) + ("uwuplus",)
    try:
        items = parse_download_source_order(row.value, allowed=allowed)
    except ValueError:
        return
    rewritten = items
    stripped = (row.value or "").strip()
    from mediatracker.providers.sources.stremio_addons_config import replace_uwuplus_in_source_order

    if "uwuplus" in rewritten:
        rewritten = replace_uwuplus_in_source_order(rewritten)
    normalized = ",".join(rewritten)
    # Do not rewrite factory default still equal to
    # wawacity,free_telecharger,hive,comet,torrentio,stremio_addons — existing instances keep FT active until manual opt-out.
    if stripped == DOWNLOAD_SOURCE_ORDER_WITH_UWUPLUS:
        normalized = DEFAULT_DOWNLOAD_SOURCE_ORDER
    elif normalized in {LEGACY_DOWNLOAD_SOURCE_ORDER, DOWNLOAD_SOURCE_ORDER_WITH_UWUPLUS}:
        normalized = DEFAULT_DOWNLOAD_SOURCE_ORDER
    if normalized == (row.value or "").strip():
        return
    row.value = normalized
    row.updated_at = datetime.now(timezone.utc)


def ensure_settings(db: Session, settings: Settings) -> None:
    now = datetime.now(timezone.utc)
    for key, spec in SETTING_SPECS.items():
        if db.get(Setting, key) is not None:
            continue
        if key in {"comet_base_urls", "uwuplus_base_url", "stremio_addons"} or key in _OPTIONAL_UNSEEDED:
            continue
        raw = _env_value(settings, key, spec)
        if spec.kind == "bool":
            text = "true" if raw else "false"
        else:
            text = "" if raw is None else str(raw)
        if spec.generate and not text:
            text = secrets.token_urlsafe(32)
        stored = encrypt_secret(text, settings.secret_key) if spec.secret and text else text
        db.add(Setting(key=key, value=stored, is_secret=spec.secret, updated_at=now))
    db.flush()
    from mediatracker.providers.sources.comet_urls import migrate_comet_urls_setting

    migrate_comet_urls_setting(db, settings)
    from mediatracker.providers.sources.stremio_addons_config import migrate_uwuplus_into_stremio_addons

    migrate_uwuplus_into_stremio_addons(db, settings)
    migrate_download_source_order_setting(db)
    db.commit()
    write_aria2_secret_file(db, settings)


def _setting_default_for_api(settings: Settings, key: str, spec: SettingSpec) -> Any:
    if not hasattr(settings, spec.attr):
        return None
    raw = _env_value(settings, key, spec)
    if raw is None:
        return None
    if spec.kind == "bool":
        return bool(raw)
    if spec.kind == "int":
        return int(raw)
    if isinstance(raw, Path):
        return str(raw)
    return raw


def _setting_bounds_for_api(key: str) -> dict[str, int] | None:
    if key not in _INT_BOUNDS:
        return None
    low, high = _INT_BOUNDS[key]
    return {"min": low, "max": high}


def list_settings(db: Session, settings: Settings, secret_key: str) -> list[dict[str, Any]]:
    rows = {row.key: row for row in db.scalars(select(Setting)).all()}
    items: list[dict[str, Any]] = []
    for key in sorted(_visible_setting_specs()):
        spec = SETTING_SPECS[key]
        row = rows.get(key)
        raw = row.value if row and row.value else ""
        if spec.secret:
            plain = decrypt_secret(raw, secret_key) if raw else ""
            items.append({"key": key, "value": None, "secret": True, "set": bool(plain)})
            continue
        if key == "auto_delete_enabled" and (row is None or not str(raw).strip()):
            item: dict[str, Any] = {"key": key, "value": True, "secret": False, "set": False}
            bounds = _setting_bounds_for_api(key)
            if bounds:
                item.update(bounds)
            default = _setting_default_for_api(settings, key, spec)
            if spec.kind == "bool":
                item["default"] = bool(default) if default is not None else False
            elif default is not None:
                item["default"] = default
            items.append(item)
            continue
        value: Any = raw
        if spec.kind == "bool":
            value = raw == "true"
        elif spec.kind == "int" and value != "":
            value = int(value)
        item = {"key": key, "value": value, "secret": False, "set": value not in ("", None)}
        bounds = _setting_bounds_for_api(key)
        if bounds:
            item.update(bounds)
        if not spec.secret:
            default = _setting_default_for_api(settings, key, spec)
            if spec.kind == "bool":
                item["default"] = bool(default) if default is not None else False
            elif default is not None:
                item["default"] = default
        items.append(item)
    return items


def update_settings(db: Session, settings: Settings, values: dict[str, Any]) -> None:
    if any(key.startswith("system.") for key in values):
        raise SettingsError("Clé de réglage inconnue.")

    work = dict(values)
    transient_activation = work.pop(TRANSIENT_ACTIVATION_NAME_KEY, None)
    if transient_activation is not None and not isinstance(transient_activation, str):
        raise SettingsError("« hive_activation_name » doit être une chaîne.")

    unknown = sorted(set(work) - set(SETTING_SPECS))
    if unknown:
        raise SettingsError("Clé de réglage inconnue.")
    reserved_in = sorted(set(work) & RESERVED_HIVE_KEYS)
    if reserved_in:
        raise SettingsError("Ce réglage Hive est géré automatiquement.")

    if hive_identity_is_lost(db, settings):
        touched = set(work) & HIVE_SETTING_KEYS
        if touched:
            only_disable = touched == {HIVE_ENABLED_KEY} and parse_bool_setting(work.get(HIVE_ENABLED_KEY)) is False
            if not only_disable:
                raise SettingsError(
                    "Identité Hive perdue : la clé privée est absente ou illisible. "
                    "Réinitialisez l’identité via POST /api/v1/hive/identity/reset."
                )

    existing = {row.key: (row.value or "").strip() for row in db.scalars(select(Setting)).all()}

    if HIVE_ONLY_KEY in work:
        requested = parse_bool_setting(work[HIVE_ONLY_KEY])
        if requested:
            if HIVE_ENABLED_KEY in work:
                hive_on = parse_bool_setting(work[HIVE_ENABLED_KEY])
            else:
                hive_on = (existing.get(HIVE_ENABLED_KEY) or "").lower() == "true"
            if not hive_on:
                raise SettingsError("Activez Hive avant d’activer « Ruche seule ».")

    try:
        prepare_hive_settings_update(
            db,
            settings,
            work,
            activation_name=transient_activation.strip() if isinstance(transient_activation, str) else None,
        )
    except ValueError as exc:
        raise SettingsError(str(exc)) from exc

    now = datetime.now(timezone.utc)
    enabled_was = (existing.get(HIVE_ENABLED_KEY) or "").lower() == "true"

    for key, new in work.items():
        if new is None:
            continue
        spec = SETTING_SPECS[key]
        if key == PROFILE_FALLBACK_ORDER_KEY:
            if not isinstance(new, str):
                raise SettingsError(f"« {key} » doit être une chaîne.")
            try:
                ids = parse_profile_fallback_order(new.strip())
            except ValueError as exc:
                raise SettingsError(str(exc)) from exc
            validate_profile_fallback_ids(db, ids)
        row = db.get(Setting, key)
        if row is None:
            row = Setting(key=key, value="", is_secret=spec.secret, updated_at=now)
            db.add(row)
        if spec.secret:
            if not isinstance(new, str):
                raise SettingsError(f"Le secret « {key} » doit être une chaîne.")
            row.value = "" if new == "" else encrypt_secret(new, settings.secret_key)
        else:
            if key == HIVE_INSTANCE_NAME_KEY and isinstance(new, str) and not new.strip():
                if (row.value or "").strip():
                    raise SettingsError("Le nom d'instance Hive ne peut pas être effacé.")
                continue
            row.value = _normalize(spec, key, new)
        row.is_secret = spec.secret
        row.updated_at = now

    enabled_now = _setting_row_text(db, HIVE_ENABLED_KEY).lower() == "true"
    if enabled_now and not enabled_was and not _setting_row_text(db, HIVE_INSTANCE_NAME_KEY):
        raise SettingsError("Un nom d'instance est requis pour activer Hive.")

    if enabled_was and not enabled_now:
        from mediatracker.hive.catalog import strip_hive_keep_flags, tombstone_all_local_entries

        tombstone_all_local_entries(db, settings, now=datetime.now(timezone.utc))
        strip_hive_keep_flags(db)

    from mediatracker.hive.catalog_sync_gate import mark_hive_catalog_sync_needed

    if enabled_now and not enabled_was:
        mark_hive_catalog_sync_needed(db)
    if "hive_public_address" in work and work["hive_public_address"] is not None:
        old_addr = (existing.get("hive_public_address") or "").strip().rstrip("/")
        new_addr = _setting_row_text(db, "hive_public_address").strip().rstrip("/")
        if new_addr != old_addr:
            mark_hive_catalog_sync_needed(db)
    if "hive_catalog_auto_share" in work and work["hive_catalog_auto_share"] is not None:
        old_auto = (existing.get("hive_catalog_auto_share") or "").lower() == "true"
        new_auto = _setting_row_text(db, "hive_catalog_auto_share").lower() == "true"
        if old_auto != new_auto:
            mark_hive_catalog_sync_needed(db)

    from mediatracker.providers.sources.comet_urls import sync_comet_provider_config
    from mediatracker.providers.sources.loading import apply_source_base_urls

    apply_source_base_urls(
        db, {key: value for key, value in work.items() if value is not None}
    )
    if "comet_base_urls" in work and work["comet_base_urls"] is not None:
        from mediatracker.providers.sources.comet_urls import parse_comet_url_setting_text

        sync_comet_provider_config(db, parse_comet_url_setting_text(_setting_row_text(db, "comet_base_urls")))
        first = parse_comet_url_setting_text(_setting_row_text(db, "comet_base_urls"))
        if first:
            legacy = db.get(Setting, "comet_base_url")
            if legacy is not None:
                legacy.value = first[0]
    if "stremio_addons" in work and work["stremio_addons"] is not None:
        from mediatracker.providers.sources.stremio_addons_config import (
            parse_stremio_addons_setting,
            sync_stremio_addons_provider_config,
        )

        sync_stremio_addons_provider_config(
            db, parse_stremio_addons_setting(_setting_row_text(db, "stremio_addons"))
        )
    db.commit()
    if any(key in work for key in ("tmdb_display_language", "tmdb_region", "tmdb_original_languages")):
        # Shared TMDB clients are keyed by display language: no need to close them.
        from mediatracker.core.tmdb_trial_cache import clear_tmdb_trial_cache

        clear_tmdb_trial_cache()
    if "aria2_rpc_secret" in values:
        write_aria2_secret_file(db, settings)


def write_aria2_secret_file(db: Session, settings: Settings) -> None:
    row = db.get(Setting, "aria2_rpc_secret")
    if row is None or not row.value:
        return
    secret = decrypt_secret(row.value, settings.secret_key)
    if not secret:
        return
    path = Path(settings.config_dir) / "aria2.secret"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(secret, encoding="utf-8")
    path.chmod(0o600)


def _env_value(settings: Settings, key: str, spec: SettingSpec):
    """MOVIES_DIR and SERIES_DIR are the names shown to the user. The older keys still seed."""
    if key == "library_movies_path":
        return settings.movies_dir or settings.library_movies_path
    if key == "library_series_path":
        return settings.series_dir or settings.library_series_path
    return getattr(settings, spec.attr, None)


_DIR_KEYS = frozenset({"media_dir", "library_movies_path", "library_series_path", "downloads_dir", "trash_dir"})


def _absolute_dir(key: str, text: str) -> str:
    if key not in _DIR_KEYS:
        raise SettingsError(f"« {key} » n'est pas un dossier.")
    if "\x00" in text or len(text) > 500 or not text.startswith("/"):
        raise SettingsError(f"« {key} » doit être un chemin absolu.")
    if ".." in PurePosixPath(text).parts or text.rstrip("/") == "":
        raise SettingsError(f"« {key} » n'est pas utilisable.")
    return text.rstrip("/")


def _choices(key: str, text: str, options: tuple[str, ...]) -> str:
    try:
        return ",".join(parse_choice_list(text, options))
    except ValueError as exc:
        raise SettingsError(f"« {key} » : {exc}") from exc


def _normalize(spec: SettingSpec, key: str, value: Any) -> str:
    if spec.kind == "bool":
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, str) and value.strip().lower() in {"true", "false", "1", "0"}:
            return "true" if value.strip().lower() in {"true", "1"} else "false"
        raise SettingsError(f"« {key} » doit être un booléen.")
    if spec.kind == "int":
        if isinstance(value, bool) or not isinstance(value, int | str):
            raise SettingsError(f"« {key} » doit être un entier.")
        try:
            number = int(value)
        except ValueError as exc:
            raise SettingsError(f"« {key} » doit être un entier.") from exc
        low, high = _INT_BOUNDS[key]
        if not low <= number <= high:
            raise SettingsError(f"« {key} » doit être compris entre {low} et {high}.")
        return str(number)
    if key == "comet_base_urls":
        from mediatracker.providers.sources.comet_urls import loads_comet_urls_from_api

        return loads_comet_urls_from_api(value)
    if key == "stremio_addons":
        from mediatracker.providers.sources.stremio_addons_config import (
            dumps_stremio_addons_for_storage,
            parse_stremio_addons_setting,
        )

        return dumps_stremio_addons_for_storage(parse_stremio_addons_setting(value))
    if not isinstance(value, str):
        raise SettingsError(f"« {key} » doit être une chaîne.")
    text = value.strip()
    if not text:
        if spec.optional:
            return ""
        raise SettingsError(f"« {key} » ne peut pas être vide.")
    if key == "default_quality" and text not in QUALITIES:
        raise SettingsError("Qualité inconnue.")
    if key == "default_language" and text not in LANGUAGES:
        raise SettingsError("Langue inconnue.")
    if key == "catalog_quality":
        if not text:
            return ""
        if text not in QUALITIES:
            raise SettingsError("Qualité inconnue.")
        return text
    if key == "catalog_language":
        if not text:
            return ""
        if text not in LANGUAGES:
            raise SettingsError("Langue inconnue.")
        return text
    if key == "tmdb_display_language":
        from mediatracker.core.tmdb.catalog_settings import validate_display_language

        try:
            return validate_display_language(text)
        except ValueError as exc:
            raise SettingsError(str(exc)) from exc
    if key == "tmdb_region":
        from mediatracker.core.tmdb.catalog_settings import validate_region

        try:
            return validate_region(text)
        except ValueError as exc:
            raise SettingsError(str(exc)) from exc
    if key == "tmdb_original_languages":
        from mediatracker.core.tmdb.catalog_settings import validate_original_languages

        try:
            return validate_original_languages(text)
        except ValueError as exc:
            raise SettingsError(str(exc)) from exc
    if key in {"ghcr_read_token", "github_update_token", "watchtower_token"}:
        if any(char.isspace() for char in text) or len(text) > 300:
            raise SettingsError("Jeton refusé.")
        return text
    if key == "watchtower_url":
        if not text:
            return ""
        if len(text) > 300 or not text.startswith(("http://", "https://")):
            raise SettingsError("URL Watchtower invalide.")
        parts = urlsplit(text)
        if parts.username or parts.password:
            raise SettingsError("URL Watchtower invalide.")
        return text.rstrip("/")
    if key == "downloads_completed_action":
        if text not in {"archive", "remove"}:
            raise SettingsError("Choisissez Archiver ou Supprimer de la liste.")
        return text
    if key == "hoster_preference":
        from mediatracker.integrations.alldebrid_public_hosts import allowed_hoster_names

        try:
            return ",".join(parse_hoster_preference(text, allowed=allowed_hoster_names()))
        except ValueError as exc:
            raise SettingsError(str(exc)) from exc
    if key == "download_source_order":
        try:
            return ",".join(parse_download_source_order(text))
        except ValueError as exc:
            raise SettingsError(str(exc)) from exc
    if key == PROFILE_FALLBACK_ORDER_KEY:
        try:
            return format_profile_fallback_order(parse_profile_fallback_order(text))
        except ValueError as exc:
            raise SettingsError(str(exc)) from exc
    if key in {"wawacity_user_agent", "free_telecharger_user_agent"}:
        if len(text) > 300:
            raise SettingsError("User-Agent trop long.")
        return text
    if key in {"media_dir", "library_movies_path", "library_series_path", "downloads_dir", "trash_dir"}:
        return _absolute_dir(key, text)
    if key == "jellyfin_path_map":
        from mediatracker.integrations.paths import format_path_map, parse_path_map

        try:
            return format_path_map(parse_path_map(text))
        except ValueError as exc:
            raise SettingsError(str(exc)) from exc
    if key in {
        "jellyfin_url",
        "wawacity_base_url",
        "comet_base_url",
        "torrentio_base_url",
        "uwuplus_base_url",
        "public_base_url",
    } and not (text.startswith("http://") or text.startswith("https://")):
        raise SettingsError(f"« {key} » doit commencer par http:// ou https://.")
    if key in {"jellyfin_url", "wawacity_base_url", "comet_base_url", "torrentio_base_url", "uwuplus_base_url"}:
        try:
            validate_outbound_url_static(text)
        except OutboundUrlRejected:
            raise SettingsError(settings_rejection_message()) from None
    if key == "public_base_url":
        parts = urlsplit(text)
        if parts.username or parts.password or not parts.hostname:
            raise SettingsError("L'URL publique ne doit pas contenir d'identifiant.")
        return text.rstrip("/")
    if key == "hive_public_address":
        if not text:
            return ""
        if len(text) > 500 or not (text.startswith("http://") or text.startswith("https://")):
            raise SettingsError("« hive_public_address » doit commencer par http:// ou https://.")
        parts = urlsplit(text)
        if parts.username or parts.password or not parts.hostname:
            raise SettingsError("L'adresse publique Hive ne doit pas contenir d'identifiant.")
        return text.rstrip("/")
    if key == "hive_instance_name":
        if not text:
            return ""
        try:
            return validate_instance_name(text)
        except ValueError as exc:
            raise SettingsError(str(exc)) from exc
    if key in {"hive_name_document", "hive_name_signature"}:
        return text
    if key in {"notify_quiet_start", "notify_quiet_end"} and not _CLOCK.match(text):
        raise SettingsError(f"« {key} » doit être une heure HH:MM.")
    if key == "notify_timezone":
        try:
            ZoneInfo(text)
        except ZoneInfoNotFoundError as exc:
            raise SettingsError("Fuseau horaire inconnu.") from exc
        return text
    return text
