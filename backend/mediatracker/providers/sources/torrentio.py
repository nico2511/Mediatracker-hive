"""Torrentio download source. Public Stremio addon JSON in, magnet links out.

No debrid key is sent: only ``{base}/stream/movie/{imdb}.json`` or
``{base}/stream/series/{imdb}:{season}:{episode}.json``. MediaTracker builds
the magnet and unlocks via AllDebrid, like Comet without a key.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, field_validator

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
    normalize_comet_url,
    select_streams,
    shared_comet_pacer,
)
from mediatracker.providers.sources.throttle import SourcePacer

logger = logging.getLogger(__name__)

TORRENTIO_CACHE_MAX_ENTRIES = 512
TORRENTIO_REVALIDATE_SECONDS = 3600.0
_TORRENTIO_CACHES: dict[str, CometPathCache] = {}


def shared_torrentio_cache(base_url: str) -> CometPathCache:
    root = base_url.strip().rstrip("/")
    cache = _TORRENTIO_CACHES.get(root)
    if cache is None:
        cache = CometPathCache(
            max_entries=TORRENTIO_CACHE_MAX_ENTRIES,
            revalidate_seconds=TORRENTIO_REVALIDATE_SECONDS,
        )
        cache.bind_domain(root)
        _TORRENTIO_CACHES[root] = cache
    return cache


def normalize_torrentio_url(value: str) -> str:
    return normalize_comet_url(value)


class TorrentioConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = ""

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, value: str) -> str:
        text = value.strip()
        if not text:
            return ""
        return normalize_torrentio_url(text)


class TorrentioProvider:
    kind = "source"
    type = "torrentio"
    capabilities: frozenset[str] = frozenset({"episode"})
    download_rank = 21

    def __init__(self, config: TorrentioConfig | None = None) -> None:
        self.config = config or TorrentioConfig()

    @classmethod
    def config_model(cls) -> type[BaseModel]:
        return TorrentioConfig

    def health(self) -> Health:
        return Health(
            status="ok",
            code=None,
            message="Torrentio sert au téléchargement (magnets, cache AllDebrid).",
            checked_at=datetime.now(timezone.utc),
        )

    @classmethod
    def open_episode(cls, settings, *, base_url: str | None = None, transport=None):
        if base_url is not None:
            root = base_url.strip().rstrip("/")
        else:
            root = (settings.torrentio_base_url or "").strip().rstrip("/")
        if not root:
            return None
        delay = float(settings.source_request_delay_seconds)
        return TorrentioSource(root, delay=delay, transport=transport)

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


class TorrentioSource:
    """Keyless Torrentio client. Responses are cached for about an hour."""

    type = "torrentio"

    def __init__(
        self,
        base_url: str,
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

        root = base_url.strip().rstrip("/")
        parsed = urlsplit(root)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise SourceFetchError("BAD_URL", "L'URL Torrentio est invalide.", retryable=False)
        try:
            validate_outbound_url_static(root)
        except OutboundUrlRejected as exc:
            raise SourceFetchError("BAD_URL", str(exc), retryable=False) from exc
        self.base_url = root
        self.quality_profile = None
        self.last_empty_reason: str | None = None
        self.last_json_from_cache: bool = False
        self.cache = cache if cache is not None else shared_torrentio_cache(self.base_url)
        self.cache.bind_domain(self.base_url)
        self.pacer = pacer if pacer is not None else shared_comet_pacer(self.base_url, float(delay))
        self._client, self._owns_client = borrow_http_client(
            ("torrentio", self.base_url),
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
        from mediatracker.core.tmdb.imdb import imdb_id_for_stremio

        imdb = imdb_id_for_stremio(query.imdb_id)
        if imdb is None:
            return []
        path = _stream_path(query, imdb)
        if path is None:
            return []
        payload = self._fetch(path)
        raw = select_streams(payload, query, preferences, profile=self.quality_profile)
        chosen = _torrentio_candidates(raw)
        if self.quality_profile is not None and not chosen:
            from mediatracker.providers.sources.stremio_stream_messages import (
                profile_mismatch_reason,
                stream_rows_seen,
            )

            seen = stream_rows_seen(payload)
            self.last_empty_reason = profile_mismatch_reason(self.quality_profile, seen=seen) if seen > 0 else None
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
        from mediatracker.core.metrics.worker_counters import SOURCE_TORRENTIO, record_source_request
        from mediatracker.security.ssrf import OutboundUrlRejected

        self.pacer.wait()
        try:
            response = self._client.get(url)
        except OutboundUrlRejected as exc:
            record_source_request(SOURCE_TORRENTIO, error=True)
            raise SourceFetchError("BAD_URL", str(exc), retryable=False) from exc
        except httpx.HTTPError as exc:
            record_source_request(SOURCE_TORRENTIO, error=True)
            raise SourceFetchError("TORRENTIO_DOWN", "Torrentio est injoignable.", retryable=True) from exc
        if response.status_code == 404:
            record_source_request(SOURCE_TORRENTIO, error=False)
            return None, None
        if response.status_code == 403:
            record_source_request(SOURCE_TORRENTIO, error=True)
            raise SourceFetchError(
                "TORRENTIO_FORBIDDEN",
                "Torrentio a refusé la requête.",
                retryable=True,
            )
        if response.status_code != 200:
            record_source_request(SOURCE_TORRENTIO, error=True)
            raise SourceFetchError(
                "TORRENTIO_DOWN",
                f"Torrentio a répondu HTTP {response.status_code}.",
                retryable=response.status_code >= 500,
            )
        record_source_request(SOURCE_TORRENTIO, error=False)
        path = _cache_path_from_url(url, self.base_url)
        return response.text, path


def _torrentio_candidates(links: list[LinkCandidate]) -> list[LinkCandidate]:
    """Relabel Comet-shaped rows and dedupe by info-hash."""
    merged: list[LinkCandidate] = []
    seen: set[str] = set()
    for link in links:
        info = _info_hash_from_external_id(link.external_id)
        if info is None or info in seen:
            continue
        seen.add(info)
        index = "0"
        if link.external_id and ":" in link.external_id:
            parts = link.external_id.split(":")
            if len(parts) >= 3:
                index = parts[2]
        merged.append(
            LinkCandidate(
                url=link.url,
                kind=link.kind,
                quality=link.quality,
                language=link.language,
                size=link.size,
                filename=link.filename,
                season=link.season,
                episode=link.episode,
                file_index=link.file_index,
                external_id=f"torrentio:{info}:{index}",
            )
        )
    return merged


def drop_hashes_seen_in_comet(
    candidates: list[LinkCandidate],
    comet_hashes: set[str],
) -> list[LinkCandidate]:
    if not comet_hashes:
        return candidates
    kept: list[LinkCandidate] = []
    for link in candidates:
        info = _info_hash_from_external_id(link.external_id)
        if info is not None and info in comet_hashes:
            continue
        kept.append(link)
    return kept


def _info_hash_from_external_id(external_id: str | None) -> str | None:
    text = (external_id or "").strip().lower()
    for prefix in ("torrentio:", "comet:"):
        if text.startswith(prefix):
            parts = text.split(":")
            if len(parts) < 2:
                return None
            digest = parts[1].strip()
            if len(digest) == 40 and all(ch in "0123456789abcdef" for ch in digest):
                return digest
    return None


def _stream_path(query: EpisodeQuery, imdb: str) -> str | None:
    if query.media_type == "series":
        if query.season is None or query.episode is None:
            return None
        return f"stream/series/{imdb}:{query.season}:{query.episode}.json"
    return f"stream/movie/{imdb}.json"


def _cache_path_from_url(url: str, base_url: str) -> str:
    root = base_url.rstrip("/")
    if url.startswith(root + "/"):
        tail = url[len(root) + 1 :]
    else:
        tail = urlsplit(url).path.lstrip("/")
    if tail.startswith("stream/"):
        return tail
    return tail


def _json_body(body: str) -> dict[str, Any]:
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise SourceFetchError("TORRENTIO_DOWN", "Réponse Torrentio illisible.", retryable=True) from exc
    if not isinstance(payload, dict):
        raise SourceFetchError("TORRENTIO_DOWN", "Réponse Torrentio inattendue.", retryable=True)
    return payload
