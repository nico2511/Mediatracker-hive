"""Source page bindings: stable path and URL built from the current Wawacity domain."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.db.models import Media, Provider, SourceBinding
from mediatracker.providers.base import SourceResult

REVALIDATE_INTERVAL = timedelta(days=30)


def path_from_url(url: str | None) -> str | None:
    """Relative path (and query when present) from a stored absolute URL."""
    text = (url or "").strip()
    if not text:
        return None
    parsed = urlparse(text)
    if parsed.scheme and parsed.netloc:
        path = parsed.path or "/"
        if parsed.query:
            return f"{path}?{parsed.query}"
        return path
    if text.startswith("/"):
        return text
    if text.startswith("?"):
        return text
    return f"/{text}" if text else None


def url_from_path(base_url: str, path: str | None) -> str | None:
    """Absolute page URL from the configured base and a stored path."""
    root = (base_url or "").strip().rstrip("/")
    if not root:
        return None
    rel = (path or "").strip()
    if not rel:
        return None
    if rel.startswith("http://") or rel.startswith("https://"):
        return rel
    if rel.startswith("?"):
        return f"{root}/{rel}"
    if not rel.startswith("/"):
        rel = f"/{rel}"
    return f"{root}{rel}"


def binding_page_url(db: Session, settings: Settings, binding: SourceBinding) -> str | None:
    from mediatracker.providers.sources.registry import for_type

    provider = db.get(Provider, binding.provider_id)
    if provider is None:
        return (binding.url or "").strip() or None
    entry = for_type(provider.type)
    if entry is None:
        return (binding.url or "").strip() or None
    return entry.binding_page_url(db, settings, binding)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def binding_needs_revalidation(binding: SourceBinding, now: datetime) -> bool:
    """True when the binding has not been checked successfully within the interval."""
    moment = _aware(now) or datetime.now(timezone.utc)
    last = _aware(binding.last_scanned_at)
    if last is None:
        return True
    return moment - last >= REVALIDATE_INTERVAL


def binding_is_usable(binding: SourceBinding) -> bool:
    return bool(binding.active) and not (binding.last_error or "").strip()


def seasons_needed(media: Media, seasons: set[int | None]) -> set[int | None]:
    if media.type == "film":
        return {None}
    return {season for season in seasons if season is not None}


def _bindings_for(db: Session, media_id: int, provider_id: int) -> list[SourceBinding]:
    return list(
        db.scalars(
            select(SourceBinding).where(
                SourceBinding.media_id == media_id,
                SourceBinding.provider_id == provider_id,
            )
        ).all()
    )


def pick_bound_pages(
    db: Session,
    settings: Settings,
    media: Media,
    provider_id: int,
    seasons: set[int | None],
    *,
    now: datetime,
    force_search: bool,
    ignore_revalidation: bool = False,
) -> list[SourceResult] | None:
    """Known pages when every needed season has a healthy binding and revalidation is not due."""
    if force_search:
        return None
    needed = seasons_needed(media, seasons)
    if not needed:
        return None
    rows = _bindings_for(db, media.id, provider_id)
    by_season: dict[int | None, SourceBinding] = {}
    for row in rows:
        if not binding_is_usable(row):
            continue
        if not (row.path or row.url):
            continue
        key = row.season
        if media.type == "film":
            key = None
        if key in by_season:
            continue
        by_season[key] = row
    if not needed <= set(by_season):
        return None
    for season in needed:
        binding = by_season[season]
        if not ignore_revalidation and binding_needs_revalidation(binding, now):
            return None
    pages: list[SourceResult] = []
    for season in sorted(needed, key=lambda value: (value is None, value or 0)):
        binding = by_season[season]
        page_url = binding_page_url(db, settings, binding)
        if not page_url:
            return None
        ref = (binding.external_ref or page_url).strip()
        pages.append(
            SourceResult(
                title=media.title,
                year=media.year,
                media_type=media.type if media.type in {"film", "series"} else None,
                ref=ref,
                url=page_url,
                season=binding.season,
                quality=binding.quality,
                language=binding.language,
            )
        )
    return pages


def remember_binding_fields(binding: SourceBinding, page_url: str | None, *, path: str | None = None) -> None:
    """Keep the historic absolute URL and the relative path used to rebuild it."""
    if page_url:
        binding.url = page_url
    extracted = path or path_from_url(page_url)
    if extracted:
        binding.path = extracted
    binding.path_manual = False


def _host_key(base_url: str) -> str:
    parsed = urlparse(base_url.strip())
    host = (parsed.hostname or "").lower()
    port = parsed.port
    if port and port not in {80, 443}:
        return f"{host}:{port}"
    return host


def resolve_binding_path(
    db: Session,
    settings: Settings,
    *,
    url: str | None,
    path: str | None,
) -> str:
    """Turn a full URL or a relative path into the stored path. The domain must match Wawacity."""
    from mediatracker.providers.sources.loading import source_base_url

    base = source_base_url(db, settings, "wawacity")
    if not base:
        raise ValueError("L'URL Wawacity n'est pas configurée.")
    raw_path = (path or "").strip()
    raw_url = (url or "").strip()
    if raw_url:
        parsed = urlparse(raw_url)
        if parsed.scheme and parsed.netloc:
            if _host_key(raw_url) != _host_key(base):
                raise ValueError(f"L'URL doit utiliser le domaine Wawacity actuel ({urlparse(base).netloc or base}).")
            resolved = path_from_url(raw_url)
            if not resolved:
                raise ValueError("Chemin de page invalide.")
            return resolved
        if not raw_path:
            raw_path = raw_url
    if not raw_path:
        raise ValueError("Indiquez une URL complète ou un chemin relatif.")
    if raw_path.startswith("http://") or raw_path.startswith("https://"):
        if _host_key(raw_path) != _host_key(base):
            raise ValueError(f"L'URL doit utiliser le domaine Wawacity actuel ({urlparse(base).netloc or base}).")
        resolved = path_from_url(raw_path)
        if not resolved:
            raise ValueError("Chemin de page invalide.")
        return resolved
    return path_from_url(raw_path) or raw_path


def bindings_need_search(
    db: Session,
    media: Media,
    provider_id: int,
    seasons: set[int | None],
) -> bool:
    """True when a season lacks a page or the stored binding is in error."""
    needed = seasons_needed(media, seasons)
    if not needed:
        return False
    rows = _bindings_for(db, media.id, provider_id)
    by_season: dict[int | None, SourceBinding] = {}
    for row in rows:
        key = None if media.type == "film" else row.season
        if key in by_season:
            continue
        by_season[key] = row
    for season in needed:
        binding = by_season.get(season)
        if binding is None or not (binding.path or binding.url):
            return True
        if binding_state(binding) == "error":
            return True
    return False


def binding_state(binding: SourceBinding | None) -> str:
    if binding is None or not (binding.path or binding.url):
        return "missing"
    if (binding.last_error or "").strip():
        return "error"
    if binding.path_manual:
        return "manual"
    return "ok"


def serialize_binding(
    db: Session,
    settings: Settings,
    binding: SourceBinding | None,
) -> dict:
    if binding is None:
        return {
            "url": None,
            "path": None,
            "state": "missing",
            "last_error": None,
            "last_error_at": None,
            "last_scanned_at": None,
            "manual": False,
        }
    page_url = binding_page_url(db, settings, binding)
    return {
        "url": page_url,
        "path": binding.path,
        "state": binding_state(binding),
        "last_error": binding.last_error,
        "last_error_at": binding.last_error_at,
        "last_scanned_at": binding.last_scanned_at,
        "manual": bool(binding.path_manual),
    }


def wawacity_provider_id(db: Session) -> int:
    from mediatracker.providers.sources.registry import get

    return get("wawacity").provider_id(db)


def get_binding(
    db: Session,
    media_id: int,
    provider_id: int,
    season: int | None,
) -> SourceBinding | None:
    rows = list(
        db.scalars(
            select(SourceBinding).where(
                SourceBinding.media_id == media_id,
                SourceBinding.provider_id == provider_id,
                SourceBinding.active.is_(True),
            )
        ).all()
    )
    if season is None:
        for row in rows:
            if row.season is None:
                return row
        return rows[0] if len(rows) == 1 else None
    for row in rows:
        if row.season == season:
            return row
    return None


def upsert_binding_path(
    db: Session,
    settings: Settings,
    media: Media,
    *,
    season: int | None,
    stored_path: str,
) -> SourceBinding:
    from mediatracker.providers.sources.loading import source_base_url

    provider_id = wawacity_provider_id(db)
    row = get_binding(db, media.id, provider_id, season)
    page_url = url_from_path(source_base_url(db, settings, "wawacity"), stored_path)
    ref = (stored_path or page_url or "")[:300]
    if row is None:
        row = SourceBinding(
            media_id=media.id,
            provider_id=provider_id,
            external_ref=ref,
            season=season if media.type == "series" else None,
            active=True,
        )
        db.add(row)
    row.path = stored_path
    row.path_manual = True
    row.last_error = None
    row.last_error_at = None
    row.external_ref = ref
    if page_url:
        row.url = page_url
    return row


@dataclass(frozen=True)
class SourceBindingProbe:
    accessible: bool
    title: str | None
    links_by_hoster: dict[str, int]
    message: str | None = None


def probe_binding_page(
    db: Session,
    settings: Settings,
    page_url: str,
    *,
    transport: httpx.BaseTransport | None = None,
) -> SourceBindingProbe:
    """One paced GET on the page. Allowed hosters only; uptobox is ignored."""
    from mediatracker.providers.sources.registry import get

    return get("wawacity").probe_binding_page(db, settings, page_url, transport=transport)
