"""Generic Stremio addons: one module, several addon base URLs.

Each enabled addon is called once per search on ``/stream/movie|series/…``. Torrent
rows reuse Comet's ``select_streams``; DDL rows reuse UwU+'s ``select_uwu_streams``.
Keys are never sent: pasted URLs are stripped and outbound requests are checked.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from mediatracker.core.quality_profile import Profile
from mediatracker.core.tmdb.imdb import imdb_id_for_stremio
from mediatracker.providers.base import (
    CapabilityNotSupported,
    EpisodeQuery,
    Health,
    LinkCandidate,
    MediaType,
    ReleaseListing,
    SourceFetchError,
    SourceItem,
    SourcePreferences,
    SourceRef,
    SourceResult,
    SourceTitle,
)
from mediatracker.providers.sources.comet import (
    CometPathCache,
    select_streams,
    shared_comet_pacer,
)
from mediatracker.providers.sources.stremio_addons_config import (
    StremioAddonEntry,
    normalize_stremio_addon_list,
    parse_stremio_addons_setting,
    strip_secret_segments,
    validate_stremio_addon_url,
)
from mediatracker.providers.sources.stremio_stream_messages import (
    addon_no_source_reason,
    profile_mismatch_reason,
    stream_rows_seen,
)
from mediatracker.providers.sources.throttle import SourcePacer
from mediatracker.providers.sources.uwuplus import (
    _SECRET_IN_URL,
    _cache_path_from_url,
    _json_body,
    _stream_path,
    select_uwu_streams,
)

logger = logging.getLogger(__name__)

STREMIO_ADDONS_CACHE_MAX_ENTRIES = 512
STREMIO_ADDONS_REVALIDATE_SECONDS = 3600.0
_STREMIO_ADDONS_CACHES: dict[str, CometPathCache] = {}
_HASH = re.compile(r"^[a-f0-9]{40}$")
_EXTERNAL_PREFIX = re.compile(r"^(?:comet|torrentio|uwuplus):")


class StremioAddonItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = ""
    enabled: bool = True

    @field_validator("url")
    @classmethod
    def _url(cls, value: str) -> str:
        text = value.strip()
        if not text:
            return ""
        return validate_stremio_addon_url(text)


class StremioAddonsConfig(BaseModel):
    """``base_url`` mirrors the first addon for provider bootstrap; ``addons`` is authoritative."""

    model_config = ConfigDict(extra="forbid")

    base_url: str = ""
    addons: list[StremioAddonItem] = Field(default_factory=list)

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, value: str) -> str:
        text = value.strip()
        if not text:
            return ""
        return validate_stremio_addon_url(text)

    @field_validator("addons")
    @classmethod
    def _addons(cls, value: list[StremioAddonItem]) -> list[StremioAddonItem]:
        entries = [
            StremioAddonEntry(url=item.url, enabled=item.enabled)
            for item in value
            if item.url.strip()
        ]
        normalized = normalize_stremio_addon_list(entries)
        return [StremioAddonItem(url=entry.url, enabled=entry.enabled) for entry in normalized]


def shared_stremio_addons_cache(base_url: str) -> CometPathCache:
    root = base_url.strip().rstrip("/")
    cache = _STREMIO_ADDONS_CACHES.get(root)
    if cache is None:
        cache = CometPathCache(
            max_entries=STREMIO_ADDONS_CACHE_MAX_ENTRIES,
            revalidate_seconds=STREMIO_ADDONS_REVALIDATE_SECONDS,
        )
        cache.bind_domain(root)
        _STREMIO_ADDONS_CACHES[root] = cache
    return cache


def select_stremio_addon_streams(
    payload: dict[str, Any],
    query: EpisodeQuery,
    preferences: SourcePreferences,
    *,
    profile: Profile | None = None,
    external_prefix: str = "stremio_addons",
) -> list[LinkCandidate]:
    torrents = select_streams(payload, query, preferences, profile=profile)
    ddls = select_uwu_streams(payload, query, preferences, profile=profile)
    merged: list[LinkCandidate] = []
    seen: set[str] = set()
    for link in [*torrents, *ddls]:
        key = _dedupe_key(link)
        if key is None or key in seen:
            continue
        seen.add(key)
        link.external_id = _remap_external_id(link.external_id, external_prefix, key)
        merged.append(link)
    merged.sort(key=_sort_key)
    return merged


def _dedupe_key(link: LinkCandidate) -> str | None:
    if link.kind == "magnet" and link.external_id:
        digest = _info_hash_from_any_external_id(link.external_id)
        if digest:
            return f"magnet:{digest}"
    if link.external_id:
        return link.external_id.split(":")[-1] if ":" in link.external_id else link.external_id
    return link.url


def _info_hash_from_any_external_id(external_id: str) -> str | None:
    text = (external_id or "").strip().lower()
    if not text:
        return None
    parts = text.split(":")
    for part in reversed(parts):
        if _HASH.match(part):
            return part
    return None


def _remap_external_id(external_id: str | None, prefix: str, dedupe: str) -> str:
    text = (external_id or "").strip()
    if text and _EXTERNAL_PREFIX.match(text):
        tail = text.split(":", 1)[1]
        return f"{prefix}:{tail}"
    return f"{prefix}:{dedupe}"


def _sort_key(link: LinkCandidate) -> tuple:
    kind_rank = 0 if link.kind == "magnet" else 1
    return (kind_rank, -(link.size or 0), link.external_id or link.url)


class StremioAddonsProvider:
    kind = "source"
    type = "stremio_addons"
    capabilities: frozenset[str] = frozenset({"episode"})
    download_rank = 23

    def __init__(self, config: StremioAddonsConfig | None = None) -> None:
        self.config = config or StremioAddonsConfig()

    @classmethod
    def config_model(cls) -> type[BaseModel]:
        return StremioAddonsConfig

    def health(self) -> Health:
        return Health(
            status="ok",
            code=None,
            message="Addons Stremio génériques (torrents et hébergeurs via AllDebrid).",
            checked_at=datetime.now(timezone.utc),
        )

    @classmethod
    def open_episode(
        cls,
        settings,
        *,
        base_url: str | None = None,
        addons: list[StremioAddonEntry] | None = None,
        transport=None,
        request_delay_seconds: float | None = None,
    ):
        entries = list(addons or [])
        if not entries and base_url:
            entries = [StremioAddonEntry(url=base_url.strip().rstrip("/"), enabled=True)]
        enabled = [entry for entry in entries if entry.enabled and entry.url.strip()]
        if not enabled:
            return None
        delay = float(
            settings.source_request_delay_seconds
            if request_delay_seconds is None
            else request_delay_seconds
        )
        return StremioAddonsAggregateSource(enabled, delay=delay, transport=transport)

    def search(self, query: str, media_type: MediaType | None) -> list[SourceResult]:
        raise CapabilityNotSupported(self.type, "search")

    def get_title(self, ref: SourceRef) -> SourceTitle:
        raise CapabilityNotSupported(self.type, "items")

    def list_items(self, ref: SourceRef) -> list[SourceItem]:
        raise CapabilityNotSupported(self.type, "items")

    def get_links(self, ref: SourceRef) -> list[LinkCandidate]:
        raise CapabilityNotSupported(self.type, "links")

    def latest_releases(self, media_type: MediaType) -> list[ReleaseListing]:
        del media_type
        return []


class StremioAddonSource:
    """One Stremio addon instance. JSON cached per domain; a single GET per stream path."""

    type = "stremio_addons"

    def __init__(
        self,
        entry: StremioAddonEntry,
        *,
        delay: float = 0.0,
        transport: httpx.BaseTransport | None = None,
        cache: CometPathCache | None = None,
        pacer: SourcePacer | None = None,
        timeout: float = 12.0,
    ) -> None:
        from mediatracker.core.outbound_http import borrow_http_client
        from mediatracker.security.ssrf import (
            OutboundUrlRejected,
            httpx_request_event_hooks,
            validate_outbound_url_static,
        )

        root = entry.url.strip().rstrip("/")
        try:
            validate_outbound_url_static(root)
        except OutboundUrlRejected as exc:
            raise SourceFetchError("BAD_URL", str(exc), retryable=False) from exc
        self.entry = entry
        self.base_url = root
        self.quality_profile: Profile | None = None
        self.last_empty_reason: str | None = None
        self.last_json_from_cache: bool = False
        self.cache = cache if cache is not None else shared_stremio_addons_cache(self.base_url)
        self.cache.bind_domain(self.base_url)
        self.pacer = pacer if pacer is not None else shared_comet_pacer(self.base_url, float(delay))
        client_key = ("stremio_addons", self.base_url)
        self._client, self._owns_client = borrow_http_client(
            client_key,
            httpx.Client,
            transport=transport,
            timeout=timeout,
            follow_redirects=False,
            event_hooks=httpx_request_event_hooks(),
            headers={"Accept": "application/json", "User-Agent": "MediaTracker"},
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def links_for(self, query: EpisodeQuery, preferences: SourcePreferences) -> list[LinkCandidate]:
        imdb = imdb_id_for_stremio(query.imdb_id)
        if imdb is None:
            return []
        path = _stream_path(query, imdb)
        if path is None:
            return []
        payload = self._fetch(path)
        chosen = select_stremio_addon_streams(
            payload,
            query,
            preferences,
            profile=self.quality_profile,
        )
        seen = stream_rows_seen(payload)
        if self.quality_profile is not None and not chosen:
            if seen > 0:
                self.last_empty_reason = profile_mismatch_reason(self.quality_profile, seen=seen)
            else:
                self.last_empty_reason = addon_no_source_reason()
        elif not chosen:
            self.last_empty_reason = addon_no_source_reason() if seen == 0 else None
        else:
            self.last_empty_reason = None
        return chosen

    def _fetch(self, path: str) -> dict[str, Any]:
        cached = self.cache.get(path)
        if cached is not None:
            self.last_json_from_cache = True
            return _json_body(cached)
        self.last_json_from_cache = False
        url = f"{self.base_url}/{path}"
        body, used_path = self._get(url)
        if body is None:
            return {"streams": []}
        if used_path is not None:
            self.cache.set(used_path, body)
            if used_path != path:
                self.cache.set(path, body)
        return _json_body(body)

    def _get(self, url: str) -> tuple[str | None, str | None]:
        from mediatracker.core.metrics.worker_counters import SOURCE_STREMIO_ADDONS, record_source_request
        from mediatracker.security.ssrf import OutboundUrlRejected

        if _SECRET_IN_URL.search(url):
            raise SourceFetchError("BAD_URL", "Requête addon invalide (secret dans l'URL).", retryable=False)
        self.pacer.wait()
        try:
            response = self._client.get(url)
        except OutboundUrlRejected as exc:
            record_source_request(SOURCE_STREMIO_ADDONS, error=True)
            raise SourceFetchError("BAD_URL", str(exc), retryable=False) from exc
        except httpx.HTTPError as exc:
            record_source_request(SOURCE_STREMIO_ADDONS, error=True)
            raise SourceFetchError("STREMIO_ADDON_DOWN", "Addon Stremio injoignable.", retryable=True) from exc
        if response.status_code == 404:
            record_source_request(SOURCE_STREMIO_ADDONS, error=False)
            return None, None
        if response.status_code == 403:
            record_source_request(SOURCE_STREMIO_ADDONS, error=True)
            raise SourceFetchError(
                "STREMIO_ADDON_FORBIDDEN",
                "L'addon a refusé la requête.",
                retryable=True,
            )
        if response.status_code != 200:
            record_source_request(SOURCE_STREMIO_ADDONS, error=True)
            raise SourceFetchError(
                "STREMIO_ADDON_DOWN",
                f"L'addon a répondu HTTP {response.status_code}.",
                retryable=response.status_code >= 500,
            )
        record_source_request(SOURCE_STREMIO_ADDONS, error=False)
        path = _cache_path_from_url(url, self.base_url)
        return response.text, path


class StremioAddonsAggregateSource:
    """Queries every enabled addon once, then merges torrent and DDL candidates."""

    type = "stremio_addons"

    def __init__(
        self,
        addons: list[StremioAddonEntry],
        *,
        delay: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._addons = [entry for entry in addons if entry.url.strip() and entry.enabled]
        self._delay = float(delay)
        self._transport = transport
        self._opened: list[StremioAddonSource] = []
        self.quality_profile: Profile | None = None
        self.last_empty_reason: str | None = None

    def close(self) -> None:
        for source in self._opened:
            source.close()
        self._opened.clear()

    def links_for(self, query: EpisodeQuery, preferences: SourcePreferences) -> list[LinkCandidate]:
        self.close()
        if not self._addons:
            return []
        merged: list[LinkCandidate] = []
        seen: set[str] = set()
        last_reason: str | None = None
        for entry in self._addons:
            source = StremioAddonSource(
                entry,
                delay=self._delay,
                transport=self._transport,
                cache=shared_stremio_addons_cache(entry.url),
                pacer=shared_comet_pacer(entry.url, self._delay),
            )
            self._opened.append(source)
            source.quality_profile = self.quality_profile
            try:
                found = source.links_for(query, preferences)
            except SourceFetchError as exc:
                logger.info("Addon Stremio %s indisponible (%s).", entry.url, exc.code)
                last_reason = exc.message or str(exc.code)
                continue
            for link in found:
                key = _dedupe_key(link)
                if key is None or key in seen:
                    continue
                seen.add(key)
                merged.append(link)
            if source.last_empty_reason:
                last_reason = source.last_empty_reason
        self.last_empty_reason = last_reason if not merged else None
        return merged


def coerce_stremio_addons_config(raw: object) -> StremioAddonsConfig:
    if isinstance(raw, dict) and "addons" in raw:
        return StremioAddonsConfig.model_validate(raw)
    entries = parse_stremio_addons_setting(raw)
    return StremioAddonsConfig(
        base_url=entries[0].url if entries else "",
        addons=[
            StremioAddonItem(url=item.url, enabled=item.enabled) for item in entries
        ],
    )


def normalize_addon_url_for_paste(value: str) -> str:
    return validate_stremio_addon_url(strip_secret_segments(value))