"""Ordered Comet instance URLs from settings, provider config, and legacy fallbacks."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.core.public_source_urls import DEFAULT_COMET_INSTANCE_URLS, DEFAULT_COMET_PRIMARY
from mediatracker.db.models import Provider, Setting
from mediatracker.providers.sources.comet import normalize_comet_url
from mediatracker.security.ssrf import OutboundUrlRejected, validate_outbound_url_static
from mediatracker.settings_store import SettingsError

if TYPE_CHECKING:
    from mediatracker.config import Settings

_SETTING_KEY = "comet_base_urls"
_LEGACY_KEY = "comet_base_url"
_MIGRATED_KEY = "comet_urls_migrated"


def normalize_comet_url_list(values: list[str]) -> list[str]:
    """Validate, normalize without trailing slash, dedupe while preserving order."""
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in values:
        text = str(raw or "").strip()
        if not text:
            continue
        try:
            clean = validate_outbound_comet_url(text)
        except SettingsError:
            raise
        except ValueError as exc:
            raise SettingsError(str(exc)) from exc
        if clean in seen:
            continue
        seen.add(clean)
        ordered.append(clean)
    return ordered


def format_comet_url_list(urls: list[str]) -> str:
    return "\n".join(normalize_comet_url_list(urls))


def parse_comet_url_setting_text(text: str) -> list[str]:
    """Accept newlines or commas between URLs (admin paste)."""
    if not text or not str(text).strip():
        return []
    chunks: list[str] = []
    for line in str(text).splitlines():
        for part in line.split(","):
            piece = part.strip()
            if piece:
                chunks.append(piece)
    return normalize_comet_url_list(chunks)


def _text(db: Session, key: str) -> str:
    row = db.get(Setting, key)
    if row is None or not row.value:
        return ""
    return row.value.strip()


def _urls_from_provider_config(config: dict | None) -> list[str]:
    if not isinstance(config, dict):
        return []
    raw_list = config.get("base_urls")
    if isinstance(raw_list, list):
        try:
            return normalize_comet_url_list([str(item) for item in raw_list])
        except ValueError:
            return []
    single = str(config.get("base_url") or "").strip()
    if not single:
        return []
    try:
        return [normalize_comet_url(single)]
    except ValueError:
        return []


def default_comet_urls_from_legacy(legacy: str) -> list[str]:
    """Built-in instances; a customized legacy URL stays first."""
    text = (legacy or "").strip()
    clean = normalize_comet_url(text) if text else ""
    seeds = list(DEFAULT_COMET_INSTANCE_URLS)
    if clean and clean != DEFAULT_COMET_PRIMARY:
        return normalize_comet_url_list([clean, *seeds])
    return normalize_comet_url_list(seeds)


def resolve_comet_base_urls(db: Session, settings: Settings) -> list[str]:
    """``comet_base_urls`` vide en base = Comet désactivé ; absent = repli défaut / legacy."""
    row = db.get(Setting, _SETTING_KEY)
    if row is not None:
        raw = (row.value or "").strip()
        if not raw:
            return []
        return parse_comet_url_setting_text(raw)
    provider = db.scalar(select(Provider).where(Provider.kind == "source", Provider.type == "comet"))
    from_provider = _urls_from_provider_config(provider.config if provider is not None else None)
    if from_provider:
        return from_provider
    legacy = _text(db, _LEGACY_KEY)
    if not legacy:
        legacy = (getattr(settings, _LEGACY_KEY, None) or "").strip()
    if legacy:
        try:
            return default_comet_urls_from_legacy(legacy)
        except ValueError:
            return default_comet_urls_from_legacy("")
    return list(DEFAULT_COMET_INSTANCE_URLS)


def _comet_urls_migrated(db: Session) -> bool:
    row = db.get(Setting, _MIGRATED_KEY)
    return row is not None and (row.value or "").strip() == "1"


def _mark_comet_urls_migrated(db: Session) -> None:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    row = db.get(Setting, _MIGRATED_KEY)
    if row is None:
        db.add(Setting(key=_MIGRATED_KEY, value="1", is_secret=False, updated_at=now))
    else:
        row.value = "1"
        row.updated_at = now


def migrate_comet_urls_setting(db: Session, settings: Settings) -> None:
    """One-shot seed from legacy URL. Never overwrites an existing ``comet_base_urls`` row."""
    row = db.get(Setting, _SETTING_KEY)
    if row is not None:
        _mark_comet_urls_migrated(db)
        return
    if _comet_urls_migrated(db):
        return
    legacy = _text(db, _LEGACY_KEY)
    if not legacy:
        legacy = (getattr(settings, _LEGACY_KEY, None) or "").strip()
    try:
        urls = default_comet_urls_from_legacy(legacy)
    except ValueError:
        return
    text = format_comet_url_list(urls)
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    db.add(Setting(key=_SETTING_KEY, value=text, is_secret=False, updated_at=now))
    _mark_comet_urls_migrated(db)


def sync_comet_provider_config(db: Session, urls: list[str]) -> None:
    """Mirror the ordered list into the Comet provider row (first URL stays in ``base_url``)."""
    from mediatracker.providers.sources.comet import CometConfig

    provider = db.scalar(select(Provider).where(Provider.kind == "source", Provider.type == "comet"))
    if provider is None:
        return
    config = dict(provider.config or {})
    clean = normalize_comet_url_list(urls)
    merged = {key: value for key, value in config.items() if key in CometConfig.model_fields}
    merged["base_urls"] = clean
    merged["base_url"] = clean[0] if clean else ""
    try:
        validated = CometConfig.model_validate(merged)
    except Exception:
        return
    provider.config = {
        "base_url": validated.base_url,
        "base_urls": list(validated.base_urls),
    }


def validate_outbound_comet_url(text: str) -> str:
    """SSRF-safe normalization used from settings_store."""
    clean = normalize_comet_url(text)
    try:
        validate_outbound_url_static(clean)
    except OutboundUrlRejected as exc:
        raise SettingsError(str(exc)) from exc
    return clean


def dumps_comet_urls_for_api(urls: list[str]) -> str:
    return format_comet_url_list(urls)


def loads_comet_urls_from_api(value: object) -> str:
    if isinstance(value, list):
        return format_comet_url_list([str(item) for item in value])
    if not isinstance(value, str):
        raise SettingsError("« comet_base_urls » doit être une chaîne ou une liste.")
    if not value.strip():
        return ""
    return format_comet_url_list(parse_comet_url_setting_text(value))
