"""Comet download source. Stremio stream JSON in, magnet links out.

Wawacity stays the catalog for discovery and nouveautés. This module only
answers ``links_for``. It never sends a debrid key: the public instance is
called as ``{base}/stream/series/{imdb}:{season}:{episode}.json``. If that
path is missing, the same call is repeated with a base64 config that names
no debrid service. MediaTracker uploads the magnet itself.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from collections import OrderedDict
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, field_validator

from mediatracker.core.quality_profile import (
    Profile,
    ReleaseCandidate,
    comet_config_token,
    detect_language,
    filter_candidates,
    normalize_resolution,
)
from mediatracker.core.source_select import canonical_quality, fold_title, release_core
from mediatracker.library.parse import parse_release, release_matches_episode, slot_from_name
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
from mediatracker.providers.sources.throttle import HtmlCache, SourcePacer

logger = logging.getLogger(__name__)

COMET_CACHE_MAX_ENTRIES = 512
COMET_REVALIDATE_SECONDS = 30 * 24 * 3600.0
_COMET_CACHES: dict[str, CometPathCache] = {}
_COMET_PACERS: dict[tuple[str, float], SourcePacer] = {}
_VIDEO = re.compile(r"(?i)\.(?:mkv|mp4|avi|m4v)$")
_RELEASE_GROUP = re.compile(r"(?i)-([a-z0-9]{2,})(?:\.(?:mkv|mp4|avi|m4v))?$")
_RELEASE_RES = re.compile(r"(?i)(2160p|1080p|720p|480p)")
_RELEASE_VF = re.compile(r"(?i)(?:vf2|vff|vfq|truefrench|(?:^|[^a-z0-9])vf(?:[^a-z0-9]|$)|french)")
_HASH = re.compile(r"^[a-f0-9]{40}$")
_NON_DEBRID_DISABLED = re.compile(r"(?i)non-debrid searches disabled")
_BANNED = re.compile(
    r"(?i)(?:^|[^a-z0-9])(?:camrip|hdcam|hdtc|hdts|telesync|telecine|dvdscr|bdscr|screener|scr|cam|ts|tc)(?:[^a-z0-9]|$)"
)
_VOSTFR = re.compile(r"(?i)(?:^|[^a-z0-9])vostfr(?:[^a-z0-9]|$)")
_VO = re.compile(r"(?i)(?:^|[^a-z0-9])vo(?:[^a-z0-9]|$)")
_FRENCH = re.compile(r"(?i)(?:^|[^a-z0-9])(?:truefrench|french|multi|vff|vfq|vf)(?:[^a-z0-9]|$)")
_FRENCH_LANG = frozenset({"VFF", "VF", "VFQ", "MULTI"})
# No debrid key. Comet then returns infoHash streams instead of unlocked URLs.
_KEYLESS = {
    "debridService": "torrent",
    "debridApiKey": "",
    "languages": {"required": [], "exclude": [], "preferred": []},
    "resolutions": {},
    "options": {
        "remove_ranks_under": -10_000_000_000,
        "allow_english_in_languages": True,
        "remove_unknown_languages": False,
    },
}


def normalize_comet_url(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    if not text.lower().startswith(("http://", "https://")):
        raise ValueError("L'URL Comet doit commencer par http:// ou https://.")
    from urllib.parse import urlsplit, urlunsplit

    from mediatracker.security.ssrf import OutboundUrlRejected, validate_outbound_url_static

    parts = urlsplit(text)
    if not parts.hostname:
        raise ValueError("L'URL Comet est invalide.")
    host = parts.hostname.lower()
    netloc = f"{host}:{parts.port}" if parts.port else host
    path = parts.path.rstrip("/")
    rebuilt = urlunsplit((parts.scheme.lower(), netloc, path, "", ""))
    try:
        validate_outbound_url_static(rebuilt)
    except OutboundUrlRejected as exc:
        raise ValueError(str(exc)) from exc
    return rebuilt


class CometPathCache:
    """In-memory Comet JSON keyed by stream path. Cleared when the base URL changes."""

    def __init__(
        self,
        *,
        max_entries: int = COMET_CACHE_MAX_ENTRIES,
        revalidate_seconds: float = COMET_REVALIDATE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_entries = max(1, int(max_entries))
        self.revalidate_seconds = max(0.0, float(revalidate_seconds))
        self._clock = clock
        self._domain: str | None = None
        self._items: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self._bypass = False

    def bind_domain(self, base_url: str) -> None:
        root = base_url.strip().rstrip("/")
        if self._domain != root:
            self._domain = root
            self._items.clear()

    def begin_bypass(self) -> None:
        self._bypass = True

    def end_bypass(self) -> None:
        self._bypass = False

    def get(self, key: str) -> str | None:
        if self._bypass:
            return None
        item = self._items.get(key)
        if item is None:
            return None
        stored_at, body = item
        if self.revalidate_seconds > 0 and self._clock() - stored_at >= self.revalidate_seconds:
            self._items.pop(key, None)
            return None
        self._items.move_to_end(key)
        return body

    def set(self, key: str, body: str) -> None:
        if self._bypass:
            return
        self._items[key] = (self._clock(), body)
        self._items.move_to_end(key)
        while len(self._items) > self.max_entries:
            self._items.popitem(last=False)


def shared_comet_cache(base_url: str) -> CometPathCache:
    root = base_url.strip().rstrip("/")
    cache = _COMET_CACHES.get(root)
    if cache is None:
        cache = CometPathCache()
        cache.bind_domain(root)
        _COMET_CACHES[root] = cache
    return cache


def shared_comet_pacer(base_url: str, delay: float) -> SourcePacer:
    root = base_url.strip().rstrip("/")
    key = (root, float(delay))
    pacer = _COMET_PACERS.get(key)
    if pacer is None:
        pacer = SourcePacer(float(delay))
        _COMET_PACERS[key] = pacer
    return pacer


class CometConfig(BaseModel):
    """No debrid key lives here. Instance URLs are provider config, not secrets."""

    model_config = ConfigDict(extra="forbid")

    base_url: str = ""
    base_urls: list[str] = []

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, value: str) -> str:
        text = value.strip()
        if not text:
            return ""
        return normalize_comet_url(text)

    @field_validator("base_urls")
    @classmethod
    def _base_urls(cls, value: list[str]) -> list[str]:
        from mediatracker.providers.sources.comet_urls import normalize_comet_url_list

        return normalize_comet_url_list(value)


class CometProvider:
    """Registry face. Discovery stays on Wawacity; this row only labels download links."""

    kind = "source"
    type = "comet"
    capabilities: frozenset[str] = frozenset({"episode"})
    download_rank = 20

    def __init__(self, config: CometConfig | None = None) -> None:
        self.config = config or CometConfig()

    @classmethod
    def config_model(cls) -> type[BaseModel]:
        return CometConfig

    def health(self) -> Health:
        return Health(
            status="ok",
            code=None,
            message="Comet sert au téléchargement (cache AllDebrid). Les nouveautés restent sur Wawacity.",
            checked_at=datetime.now(timezone.utc),
        )

    @classmethod
    def open_episode(
        cls,
        settings,
        *,
        base_url: str | None = None,
        base_urls: list[str] | None = None,
        transport=None,
    ):
        """Download-only Comet client. The catalog never calls this."""
        urls: list[str]
        if base_urls:
            urls = [item.strip().rstrip("/") for item in base_urls if item and str(item).strip()]
        elif base_url is not None and base_url.strip():
            urls = [base_url.strip().rstrip("/")]
        else:
            root = (settings.comet_base_url or "").strip().rstrip("/")
            urls = [root] if root else []
        if not urls:
            return None
        delay = float(settings.source_request_delay_seconds)
        return CometCascadeSource(urls, delay=delay, transport=transport)

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


def keyless_config_token() -> str:
    raw = json.dumps(_KEYLESS, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


class CometSource:
    """Keyless Comet client. Responses are cached for about an hour."""

    type = "comet"

    def __init__(
        self,
        base_url: str,
        *,
        transport: httpx.BaseTransport | None = None,
        cache: CometPathCache | HtmlCache | None = None,
        pacer: SourcePacer | None = None,
        timeout: float = 20.0,
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
            raise SourceFetchError("BAD_URL", "L'URL Comet est invalide.", retryable=False)
        try:
            validate_outbound_url_static(root)
        except OutboundUrlRejected as exc:
            raise SourceFetchError("BAD_URL", str(exc), retryable=False) from exc
        self.base_url = root
        self._token = keyless_config_token()
        self.quality_profile: Profile | None = None
        self.last_empty_reason: str | None = None
        self.cache = cache if cache is not None else shared_comet_cache(self.base_url)
        if hasattr(self.cache, "bind_domain"):
            self.cache.bind_domain(self.base_url)
        self.pacer = pacer if pacer is not None else SourcePacer(0)
        self._client, self._owns_client = borrow_http_client(
            ("comet", self.base_url),
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
        imdb = (query.imdb_id or "").strip()
        if not imdb.startswith("tt"):
            return []
        path = _stream_path(query, imdb)
        if path is None:
            return []
        payload = self._fetch(path, self.quality_profile)
        chosen = select_streams(payload, query, preferences, profile=self.quality_profile)
        if self.quality_profile is not None and not chosen:
            self.last_empty_reason = (
                f"Aucun flux Comet ne correspond au profil « {self.quality_profile.name} »."
            )
        else:
            self.last_empty_reason = None
        return chosen

    def _fetch(self, path: str, profile: Profile | None = None) -> dict[str, Any]:
        if profile is not None:
            return self._fetch_configured(path, profile)
        cache_key = path
        cached = self.cache.get(cache_key)
        if cached is not None:
            return _json_body(cached)
        keyless = f"{self.base_url}/{path}"
        configured = f"{self.base_url}/{self._token}/{path}"
        body, used_path = self._get(keyless)
        if body is None:
            body, used_path = self._get(configured)
        if body is None or used_path is None:
            raise SourceFetchError("COMET_DOWN", "Comet n'a pas renvoyé de flux.", retryable=True)
        self._store_cache(path, used_path, body)
        return _json_body(body)

    def _fetch_configured(self, path: str, profile: Profile) -> dict[str, Any]:
        """Ask Comet to narrow languages and resolutions, then fall back to the open list."""
        token = comet_config_token(profile)
        cache_key = f"{token}/{path}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return _json_body(cached)
        configured = f"{self.base_url}/{token}/{path}"
        body, used_path = self._get(configured)
        if body is None:
            cached = self.cache.get(path)
            if cached is not None:
                return _json_body(cached)
            body, used_path = self._get(f"{self.base_url}/{path}")
        if body is None or used_path is None:
            raise SourceFetchError("COMET_DOWN", "Comet n'a pas renvoyé de flux.", retryable=True)
        self._store_cache(cache_key, used_path, body)
        return _json_body(body)

    def _store_cache(self, path: str, used_path: str, body: str) -> None:
        self.cache.set(used_path, body)
        if used_path != path:
            self.cache.set(path, body)

    def _get(self, url: str) -> tuple[str | None, str | None]:
        from mediatracker.core.metrics.worker_counters import SOURCE_COMET, record_source_request
        from mediatracker.security.ssrf import OutboundUrlRejected

        self.pacer.wait()
        try:
            response = self._client.get(url)
        except OutboundUrlRejected as exc:
            record_source_request(SOURCE_COMET, error=True)
            raise SourceFetchError("BAD_URL", str(exc), retryable=False) from exc
        except httpx.HTTPError as exc:
            record_source_request(SOURCE_COMET, error=True)
            raise SourceFetchError("COMET_DOWN", "Comet est injoignable.", retryable=True) from exc
        if response.status_code == 404:
            record_source_request(SOURCE_COMET, error=False)
            return None, None
        if response.status_code == 403:
            record_source_request(SOURCE_COMET, error=True)
            raise SourceFetchError(
                "COMET_DISABLED",
                "Cette instance Comet refuse la recherche sans débrideur.",
                retryable=True,
            )
        if response.status_code != 200:
            record_source_request(SOURCE_COMET, error=True)
            raise SourceFetchError(
                "COMET_DOWN",
                f"Comet a répondu HTTP {response.status_code}.",
                retryable=response.status_code >= 500,
            )
        if _comet_response_disabled(response.text):
            record_source_request(SOURCE_COMET, error=True)
            raise SourceFetchError(
                "COMET_DISABLED",
                "Cette instance Comet refuse la recherche sans débrideur.",
                retryable=True,
            )
        record_source_request(SOURCE_COMET, error=False)
        path = _cache_path_from_url(url, self.base_url)
        return response.text, path


class CometCascadeSource:
    """Tries ordered Comet instances; merges magnets deduped by infoHash."""

    type = "comet"

    def __init__(
        self,
        base_urls: list[str],
        *,
        delay: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_urls = [url.strip().rstrip("/") for url in base_urls if url and str(url).strip()]
        self._delay = float(delay)
        self._transport = transport
        self._opened: list[CometSource] = []
        self.quality_profile: Profile | None = None
        self.last_empty_reason: str | None = None

    def close(self) -> None:
        for source in self._opened:
            source.close()
        self._opened.clear()

    def links_for(self, query: EpisodeQuery, preferences: SourcePreferences) -> list[LinkCandidate]:
        self.close()
        if not self._base_urls:
            return []
        merged: list[LinkCandidate] = []
        seen_hash: set[str] = set()
        last_reason: str | None = None
        used_instance: str | None = None
        for base in self._base_urls:
            source = CometSource(
                base,
                transport=self._transport,
                cache=shared_comet_cache(base),
                pacer=shared_comet_pacer(base, self._delay),
            )
            self._opened.append(source)
            source.quality_profile = self.quality_profile
            try:
                found = source.links_for(query, preferences)
            except SourceFetchError as exc:
                logger.info("Comet instance %s indisponible (%s).", base, exc.code)
                continue
            for link in found:
                info_hash = _info_hash_from_external_id(link.external_id)
                if info_hash is None or info_hash in seen_hash:
                    continue
                seen_hash.add(info_hash)
                merged.append(link)
            if merged:
                used_instance = base
                break
            last_reason = source.last_empty_reason
        self.last_empty_reason = last_reason
        if used_instance is not None:
            logger.info("Comet : instance utilisée %s (%s lien(s)).", used_instance, len(merged))
        return merged


def _info_hash_from_external_id(external_id: str | None) -> str | None:
    text = (external_id or "").strip().lower()
    if not text.startswith("comet:"):
        return None
    parts = text.split(":")
    if len(parts) < 2:
        return None
    digest = parts[1].strip()
    return digest if _HASH.match(digest) else None


def select_streams(
    payload: dict[str, Any],
    query: EpisodeQuery,
    preferences: SourcePreferences,
    profile: Profile | None = None,
) -> list[LinkCandidate]:
    """Keep magnets that match the quality profile, or the legacy preferences when none is set."""
    if profile is not None:
        return _select_for_profile(payload, query, profile)
    rows = payload.get("streams") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    found: list[LinkCandidate] = []
    seen: set[str] = set()
    for row in rows:
        candidate = _candidate(row, query, preferences)
        if candidate is None or candidate.external_id in seen:
            continue
        seen.add(candidate.external_id)
        found.append(candidate)
    four_k_first = preferences.quality == "4K"
    wanted = [signature for name in query.release_names if (signature := release_signature(name))]
    found.sort(
        key=lambda item: (
            0 if _release_exact(item.filename, wanted) else 1,
            _quality_rank(item.quality, four_k_first),
            0 if item.language == preferences.language else 1,
            -(item.size or 0),
            item.external_id,
        )
    )
    return found


def release_signature(name: str) -> tuple[str, str, str] | None:
    """Group, resolution, and MULTi/VF bucket. Missing any part is not an exact match."""
    text = (name or "").strip()
    group = _RELEASE_GROUP.search(text)
    resolution = _RELEASE_RES.search(text)
    if not group or not resolution:
        return None
    if re.search(r"(?i)multi", text):
        bucket = "multi"
    elif _RELEASE_VF.search(text):
        bucket = "vf"
    else:
        return None
    return (group.group(1).lower(), resolution.group(1).lower(), bucket)


def _release_exact(filename: str | None, wanted: list[tuple[str, str, str]]) -> bool:
    if not wanted or not filename:
        return False
    signature = release_signature(filename)
    return signature is not None and signature in wanted


def _candidate(row: object, query: EpisodeQuery, preferences: SourcePreferences) -> LinkCandidate | None:
    if not isinstance(row, dict):
        return None
    info = str(row.get("infoHash") or row.get("info_hash") or row.get("btih") or "").strip().lower()
    if not _HASH.match(info):
        return None
    hints = row.get("behaviorHints") if isinstance(row.get("behaviorHints"), dict) else {}
    filename = hints.get("filename") if isinstance(hints.get("filename"), str) else None
    if not filename:
        title = row.get("title") if isinstance(row.get("title"), str) else ""
        filename = title.splitlines()[0].strip() if title else None
    if not filename or not _VIDEO.search(filename):
        return None
    blob = " ".join(
        part
        for part in (
            filename,
            row.get("name") if isinstance(row.get("name"), str) else "",
            row.get("title") if isinstance(row.get("title"), str) else "",
        )
        if part
    )
    if _BANNED.search(blob):
        return None
    if not _matches_title(filename, query):
        return None
    file_index = row.get("fileIdx")
    if not isinstance(file_index, int):
        file_index = row.get("file_idx") if isinstance(row.get("file_idx"), int) else None
    if not _matches_slot(filename, query, file_index=file_index):
        return None
    language = _language(blob)
    if not _language_ok(language, preferences):
        return None
    quality = canonical_quality(
        str(row.get("resolution") or hints.get("resolution") or "") or filename
    )
    if not _quality_ok(quality, preferences):
        return None
    size = stream_file_size(row, hints, filename, file_index=file_index)
    index_label = str(file_index) if isinstance(file_index, int) else "0"
    return LinkCandidate(
        url=f"magnet:?xt=urn:btih:{info}",
        kind="magnet",
        quality=quality,
        language=language,
        size=size if isinstance(size, int) else None,
        filename=filename[:500],
        season=query.season,
        episode=query.episode,
        file_index=file_index if isinstance(file_index, int) else None,
        external_id=f"comet:{info}:{index_label}",
    )


def _cache_path_from_url(url: str, base_url: str) -> str:
    root = base_url.rstrip("/")
    if url.startswith(root + "/"):
        tail = url[len(root) + 1 :]
    else:
        tail = urlsplit(url).path.lstrip("/")
    if tail.startswith("stream/"):
        return tail
    if "/" in tail:
        token, remainder = tail.split("/", 1)
        if remainder.startswith("stream/"):
            return f"{token}/{remainder}"
    return tail


def _stream_path(query: EpisodeQuery, imdb: str) -> str | None:
    if query.media_type == "series":
        if query.season is None or query.episode is None:
            return None
        return f"stream/series/{imdb}:{query.season}:{query.episode}.json"
    return f"stream/movie/{imdb}.json"


def _comet_response_disabled(body: str) -> bool:
    text = body or ""
    if _NON_DEBRID_DISABLED.search(text):
        return True
    try:
        payload = json.loads(text)
    except ValueError:
        return False
    if not isinstance(payload, dict):
        return False
    for key in ("error", "message", "detail", "description"):
        value = payload.get(key)
        if isinstance(value, str) and _NON_DEBRID_DISABLED.search(value):
            return True
    return False


def _json_body(body: str) -> dict[str, Any]:
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise SourceFetchError("COMET_DOWN", "Réponse Comet illisible.", retryable=True) from exc
    if not isinstance(payload, dict):
        raise SourceFetchError("COMET_DOWN", "Réponse Comet inattendue.", retryable=True)
    return payload


def _matches_title(filename: str, query: EpisodeQuery) -> bool:
    folded = fold_title(filename)
    folded = re.sub(r"\bs\d{1,2}e\d{1,3}\b", " ", folded)
    folded = re.sub(r"\b\d{1,2}x\d{2}\b", " ", folded)
    folded = re.sub(r"\s+", " ", folded).strip()
    names = [query.title]
    if query.original_title:
        names.append(query.original_title)
    if not any(release_core(name) and release_core(name) in folded for name in names):
        return False
    if query.media_type == "film" and query.year is not None:
        return re.search(rf"(?:^|[^0-9]){query.year}(?:[^0-9]|$)", filename) is not None
    return True


def stream_file_size(
    row: dict[str, Any],
    hints: dict[str, Any],
    filename: str,
    *,
    file_index: int | None,
) -> int | None:
    """Per-file size for sanity checks. Packs never inherit the whole-torrent ``size``."""
    video_size = hints.get("videoSize")
    if isinstance(video_size, int) and video_size > 0:
        return video_size
    parsed = parse_release(filename)
    season_pack = bool(parsed.season_pack and parsed.episode is None)
    pack_with_index = isinstance(file_index, int) and parsed.season_pack
    if season_pack or pack_with_index:
        return None
    for key in ("size", "videoSize"):
        raw = row.get(key)
        if isinstance(raw, int) and raw > 0:
            return raw
    return None


def _matches_slot(filename: str, query: EpisodeQuery, *, file_index: int | None = None) -> bool:
    if query.media_type == "film":
        season, episode = slot_from_name(filename)
        return season is None and episode is None
    if query.season is None or query.episode is None:
        return False
    if not release_matches_episode(filename, season=query.season, episode=query.episode):
        return False
    parsed = parse_release(filename)
    if parsed.season_pack and parsed.episode is None:
        return isinstance(file_index, int)
    return True


def _language(blob: str) -> str | None:
    if _VOSTFR.search(blob):
        return "VOSTFR"
    if re.search(r"(?i)(?:^|[^a-z0-9])truefrench(?:[^a-z0-9]|$)", blob):
        return "VFF"
    if re.search(r"(?i)(?:^|[^a-z0-9])vff(?:[^a-z0-9]|$)", blob):
        return "VFF"
    if re.search(r"(?i)(?:^|[^a-z0-9])vfq(?:[^a-z0-9]|$)", blob):
        return "VFQ"
    if re.search(r"(?i)(?:^|[^a-z0-9])multi(?:[^a-z0-9]|$)", blob):
        return "MULTI"
    if re.search(r"(?i)(?:^|[^a-z0-9])french(?:[^a-z0-9]|$)", blob):
        return "VF"
    if _FRENCH.search(blob):
        return "VF"
    if _VO.search(blob):
        return "VO"
    return None


def _language_ok(language: str | None, preferences: SourcePreferences) -> bool:
    if language is None:
        return False
    wanted = {preferences.language}
    if preferences.allow_fallback:
        wanted.update(preferences.language_fallback)
    if language in _FRENCH_LANG and wanted & _FRENCH_LANG:
        return True
    if language == "VOSTFR" and "VOSTFR" in wanted:
        return True
    return language == "VO" and "VO" in wanted


def _quality_ok(quality: str | None, preferences: SourcePreferences) -> bool:
    if quality != "4K":
        return True
    if preferences.quality == "4K":
        return True
    return preferences.allow_fallback and "4K" in preferences.quality_fallback


def _quality_rank(quality: str | None, four_k_first: bool) -> int:
    order = {"1080p": 0, "HD": 1, "HDLight": 2, "SD": 3, "4K": 4}
    if four_k_first:
        order["4K"] = -1
    if quality is None:
        return 9
    return order.get(quality, 8)


_BAD_EXT = re.compile(r"(?i)\.(?:nfo|txt|jpg|jpeg|png|srt|sub|idx|exe|url)$")
_SEEDERS = re.compile(r"👤\s*(\d+)")


def _select_for_profile(
    payload: dict[str, Any],
    query: EpisodeQuery,
    profile: Profile,
) -> list[LinkCandidate]:
    rows = payload.get("streams") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        logger.info("Comet : 0 flux, 0 écarté(s) par le profil « %s », 0 retenu(s).", profile.name)
        return []
    parsed: list[tuple[LinkCandidate, ReleaseCandidate]] = []
    seen: set[str] = set()
    for row in rows:
        pair = _profile_candidate(row, query)
        if pair is None:
            continue
        link, candidate = pair
        if link.external_id in seen:
            continue
        seen.add(link.external_id)
        parsed.append((link, candidate))
    kept, dropped = filter_candidates([item[1] for item in parsed], profile)
    logger.info(
        "Comet : %s flux, %s écarté(s) par le profil « %s », %s retenu(s).",
        len(parsed),
        dropped,
        profile.name,
        len(kept),
    )
    return [parsed[index][0] for index in kept]


def _profile_candidate(row: object, query: EpisodeQuery) -> tuple[LinkCandidate, ReleaseCandidate] | None:
    if not isinstance(row, dict):
        return None
    info = str(row.get("infoHash") or row.get("info_hash") or row.get("btih") or "").strip().lower()
    if not _HASH.match(info):
        return None
    hints = row.get("behaviorHints") if isinstance(row.get("behaviorHints"), dict) else {}
    filename = _release_name(row, hints)
    if not filename or _BAD_EXT.search(filename):
        return None
    description = row.get("description") if isinstance(row.get("description"), str) else ""
    name = row.get("name") if isinstance(row.get("name"), str) else ""
    title = row.get("title") if isinstance(row.get("title"), str) else ""
    blob = " ".join(part for part in (filename, description, name, title) if part)
    if _BANNED.search(blob):
        return None
    if not _matches_title(filename, query):
        return None
    file_index = row.get("fileIdx")
    if not isinstance(file_index, int):
        file_index = row.get("file_idx") if isinstance(row.get("file_idx"), int) else None
    if not _matches_slot(filename, query, file_index=file_index):
        return None
    size = stream_file_size(row, hints, filename, file_index=file_index)
    index_label = str(file_index) if isinstance(file_index, int) else "0"
    language = detect_language(blob)
    resolution = normalize_resolution(f"{filename} {name}")
    link = LinkCandidate(
        url=f"magnet:?xt=urn:btih:{info}",
        kind="magnet",
        quality=resolution,
        language=language,
        size=size if isinstance(size, int) else None,
        filename=filename[:500],
        season=query.season,
        episode=query.episode,
        file_index=file_index if isinstance(file_index, int) else None,
        external_id=f"comet:{info}:{index_label}",
    )
    return link, ReleaseCandidate(
        language=language,
        resolution=resolution,
        size=size if isinstance(size, int) else None,
        seeders=_seeders(blob),
        text=blob,
    )


def _release_name(row: dict[str, Any], hints: dict[str, Any]) -> str | None:
    filename = hints.get("filename") if isinstance(hints.get("filename"), str) else None
    if filename and filename.strip():
        return filename.strip()
    description = row.get("description") if isinstance(row.get("description"), str) else ""
    for line in description.splitlines():
        cleaned = line.replace("📄", "").strip()
        if cleaned:
            return cleaned
    title = row.get("title") if isinstance(row.get("title"), str) else ""
    first = title.splitlines()[0].strip() if title else ""
    return first or None


def _seeders(blob: str) -> int | None:
    match = _SEEDERS.search(blob)
    if match is None:
        return None
    return int(match.group(1))
