"""Ordered Stremio addon instances from settings and provider config."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.db.models import Provider, Setting
from mediatracker.providers.sources.comet import normalize_comet_url
from mediatracker.providers.sources.uwuplus import _SECRET_IN_URL
from mediatracker.security.ssrf import OutboundUrlRejected, validate_outbound_url_static
from mediatracker.settings_store import SettingsError

if TYPE_CHECKING:
    from mediatracker.config import Settings

_SETTING_KEY = "stremio_addons"


@dataclass(frozen=True)
class StremioAddonEntry:
    url: str
    enabled: bool = True


def strip_secret_segments(url: str) -> str:
    """Drop query keys/values that look like debrid or tracker secrets (paste-safe URLs)."""
    text = (url or "").strip()
    if not text:
        return ""
    parts = urlsplit(text)
    query_parts = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if _SECRET_IN_URL.search(key) or _SECRET_IN_URL.search(value):
            continue
        query_parts.append((key, value))
    path_chunks = [chunk for chunk in parts.path.split("/") if chunk]
    cleaned_path: list[str] = []
    for chunk in path_chunks:
        if _SECRET_IN_URL.search(chunk):
            continue
        cleaned_path.append(chunk)
    path = "/" + "/".join(cleaned_path) if cleaned_path else ""
    rebuilt = urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            path.rstrip("/") if path != "/" else "",
            urlencode(query_parts),
            parts.fragment,
        )
    )
    return rebuilt


def validate_stremio_addon_url(text: str) -> str:
    stripped = strip_secret_segments(text)
    clean = normalize_comet_url(stripped)
    try:
        validate_outbound_url_static(clean)
    except OutboundUrlRejected as exc:
        raise SettingsError(str(exc)) from exc
    return clean


def _entry_from_dict(raw: dict[str, Any]) -> StremioAddonEntry | None:
    url_text = str(raw.get("url") or "").strip()
    if not url_text:
        return None
    try:
        url = validate_stremio_addon_url(url_text)
    except (SettingsError, ValueError):
        raise
    enabled = raw.get("enabled")
    if enabled is None:
        enabled_bool = True
    elif isinstance(enabled, bool):
        enabled_bool = enabled
    elif isinstance(enabled, str):
        enabled_bool = enabled.strip().lower() in {"1", "true", "yes", "on"}
    else:
        enabled_bool = bool(enabled)
    return StremioAddonEntry(url=url, enabled=enabled_bool)


def normalize_stremio_addon_list(entries: list[StremioAddonEntry]) -> list[StremioAddonEntry]:
    seen: set[str] = set()
    ordered: list[StremioAddonEntry] = []
    for entry in entries:
        if entry.url in seen:
            continue
        seen.add(entry.url)
        ordered.append(entry)
    return ordered


def parse_stremio_addons_setting(value: object) -> list[StremioAddonEntry]:
    if value is None:
        return []
    if isinstance(value, list):
        parsed: list[StremioAddonEntry] = []
        for item in value:
            if not isinstance(item, dict):
                raise SettingsError("Chaque addon Stremio doit être un objet {url, enabled}.")
            row = _entry_from_dict(item)
            if row is not None:
                parsed.append(row)
        return normalize_stremio_addon_list(parsed)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            payload = json.loads(text)
        except ValueError as exc:
            raise SettingsError("« stremio_addons » doit être un JSON valide.") from exc
        if not isinstance(payload, list):
            raise SettingsError("« stremio_addons » doit être une liste d'addons.")
        return parse_stremio_addons_setting(payload)
    raise SettingsError("« stremio_addons » doit être une chaîne JSON ou une liste.")


def dumps_stremio_addons_for_storage(entries: list[StremioAddonEntry]) -> str:
    normalized = normalize_stremio_addon_list(entries)
    payload = [{"url": item.url, "enabled": item.enabled} for item in normalized]
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def _addons_from_provider_config(config: dict | None) -> list[StremioAddonEntry]:
    if not isinstance(config, dict):
        return []
    raw = config.get("addons")
    if not isinstance(raw, list):
        return []
    try:
        return parse_stremio_addons_setting(raw)
    except SettingsError:
        return []


def resolve_stremio_addons(db: Session, settings: Settings) -> list[StremioAddonEntry]:
    """Empty setting row present with ``[]`` means intentionally disabled / none configured."""
    row = db.get(Setting, _SETTING_KEY)
    if row is not None:
        raw = (row.value or "").strip()
        if not raw:
            return []
        return parse_stremio_addons_setting(raw)
    provider = db.scalar(
        select(Provider).where(Provider.kind == "source", Provider.type == "stremio_addons")
    )
    return _addons_from_provider_config(provider.config if provider is not None else None)


def enabled_stremio_addons(db: Session, settings: Settings) -> list[StremioAddonEntry]:
    return [entry for entry in resolve_stremio_addons(db, settings) if entry.enabled]


def sync_stremio_addons_provider_config(db: Session, entries: list[StremioAddonEntry]) -> None:
    from mediatracker.providers.sources.stremio_addons import StremioAddonsConfig

    provider = db.scalar(
        select(Provider).where(Provider.kind == "source", Provider.type == "stremio_addons")
    )
    if provider is None:
        return
    config = dict(provider.config or {})
    clean = normalize_stremio_addon_list(entries)
    merged = {key: value for key, value in config.items() if key in StremioAddonsConfig.model_fields}
    merged["addons"] = [{"url": item.url, "enabled": item.enabled} for item in clean]
    merged["base_url"] = clean[0].url if clean else ""
    try:
        validated = StremioAddonsConfig.model_validate(merged)
    except Exception:
        return
    provider.config = {
        "base_url": validated.base_url,
        "addons": [{"url": item.url, "enabled": item.enabled} for item in validated.addons],
    }


def addon_probe_source_id(index: int) -> str:
    return f"stremio_addons:{index}"


def addon_display_label(entry: StremioAddonEntry, index: int) -> str:
    host = urlsplit(entry.url).hostname or entry.url
    return f"Addons Stremio — {host} (#{index + 1})"


_UWUPLUS_MIGRATED_KEY = "system.uwuplus_stremio_migrated"


def _uwuplus_migrated(db: Session) -> bool:
    row = db.get(Setting, _UWUPLUS_MIGRATED_KEY)
    return row is not None and (row.value or "").strip() == "1"


def _mark_uwuplus_migrated(db: Session, when) -> None:
    row = db.get(Setting, _UWUPLUS_MIGRATED_KEY)
    if row is None:
        db.add(Setting(key=_UWUPLUS_MIGRATED_KEY, value="1", is_secret=False, updated_at=when))
    else:
        row.value = "1"
        row.updated_at = when


def _legacy_uwuplus_base_url(db: Session, settings: Settings) -> str:
    row = db.get(Setting, "uwuplus_base_url")
    if row is not None and (row.value or "").strip():
        return (row.value or "").strip().rstrip("/")
    provider = db.scalar(select(Provider).where(Provider.kind == "source", Provider.type == "uwuplus"))
    if provider is not None and isinstance(provider.config, dict):
        configured = str(provider.config.get("base_url") or "").strip()
        if configured:
            return configured.rstrip("/")
    env = (getattr(settings, "uwuplus_base_url", None) or "").strip()
    return env.rstrip("/") if env else ""


def _retire_legacy_uwuplus_provider(db: Session) -> None:
    uwu_provider = db.scalar(select(Provider).where(Provider.kind == "source", Provider.type == "uwuplus"))
    if uwu_provider is not None:
        db.delete(uwu_provider)


def replace_uwuplus_in_source_order(items: list[str]) -> list[str]:
    """Swap removed ``uwuplus`` entries for ``stremio_addons`` without duplicating."""
    if "uwuplus" not in items:
        return items
    has_stremio = "stremio_addons" in items
    out: list[str] = []
    for name in items:
        if name == "uwuplus":
            if not has_stremio:
                out.append("stremio_addons")
                has_stremio = True
            continue
        if name not in out:
            out.append(name)
    return out


def migrate_uwuplus_into_stremio_addons(db: Session, settings: Settings) -> None:
    """Move a legacy UwU+ base URL into ``stremio_addons`` and retire the dedicated source."""
    from datetime import datetime, timezone

    if _uwuplus_migrated(db):
        _retire_legacy_uwuplus_provider(db)
        return
    now = datetime.now(timezone.utc)
    legacy_url = _legacy_uwuplus_base_url(db, settings)
    entries = list(resolve_stremio_addons(db, settings))
    changed_addons = False
    if legacy_url:
        try:
            clean = validate_stremio_addon_url(legacy_url)
        except (SettingsError, ValueError):
            clean = ""
        known = {item.url for item in entries}
        if clean and clean not in known:
            entries.append(StremioAddonEntry(url=clean, enabled=True))
            changed_addons = True
    if changed_addons:
        stored = dumps_stremio_addons_for_storage(entries)
        row = db.get(Setting, _SETTING_KEY)
        if row is None:
            db.add(Setting(key=_SETTING_KEY, value=stored, is_secret=False, updated_at=now))
        else:
            row.value = stored
            row.updated_at = now
        sync_stremio_addons_provider_config(db, entries)
    uwu_row = db.get(Setting, "uwuplus_base_url")
    if uwu_row is not None and (uwu_row.value or "").strip():
        uwu_row.value = ""
        uwu_row.updated_at = now
    _retire_legacy_uwuplus_provider(db)
    _mark_uwuplus_migrated(db, now)