"""UwU+ download source. Public Stremio addon JSON in, AllDebrid hoster links out.

Only ``{base}/stream/movie/{imdb}.json`` or ``{base}/stream/series/{imdb}:{s}:{e}.json``
is called. No debrid or tracker keys are sent to the addon instance. DDL streams are
selected; WEB players and the Rescraper entry are ignored. The unlock URL passed to
MediaTracker's AllDebrid client is the decoded ``externalUrl`` or the base64 ``url`` query
on ``resolve-stream`` (typically ``https://alldebrid.com/f/…``), not the wrapped addon URL.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, field_validator

from mediatracker.core.hosters import BLOCKED_SOURCE_HOSTERS, hoster_is_blocked
from mediatracker.core.quality_profile import (
    Profile,
    ReleaseCandidate,
    detect_language,
    filter_candidates,
    normalize_resolution,
)
from mediatracker.core.selection import DEFAULT_HOSTER_PREFERENCE, ensure_1fichier_last, hoster_rank
from mediatracker.core.source_select import canonical_quality
from mediatracker.core.tmdb.imdb import imdb_id_for_stremio
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
from mediatracker.providers.sources.comet import (
    CometPathCache,
    _matches_title,
    normalize_comet_url,
    shared_comet_pacer,
    stream_file_size,
)
from mediatracker.providers.sources.throttle import SourcePacer

logger = logging.getLogger(__name__)

UWUPLUS_CACHE_MAX_ENTRIES = 512
UWUPLUS_REVALIDATE_SECONDS = 3600.0
_UWUPLUS_CACHES: dict[str, CometPathCache] = {}

_VIDEO = re.compile(r"(?i)\.(?:mkv|mp4|avi|m4v)$")
_BAD_EXT = re.compile(r"(?i)\.(?:nfo|txt|jpg|jpeg|png|srt|sub|idx|exe|url|webm)$")
_BANNED = re.compile(
    r"(?i)(?:^|[^a-z0-9])(?:camrip|hdcam|hdtc|hdts|telesync|telecine|dvdscr|bdscr|screener|scr|cam|ts|tc)(?:[^a-z0-9]|$)"
)
_WEB_NAME = re.compile(r"(?i)\[web\]")
_HOSTER_LINE = re.compile(r"(?i)hébergeur\s*:\s*([^\s📺\n]+)")
_SECRET_IN_URL = re.compile(
    r"(?i)(alldebrid_key|torbox_key|nekobt_key|tr4ker_key|c411_key|v3x_key|nostradamus_key|tmdb_key)"
)


def shared_uwuplus_cache(base_url: str) -> CometPathCache:
    root = base_url.strip().rstrip("/")
    cache = _UWUPLUS_CACHES.get(root)
    if cache is None:
        cache = CometPathCache(
            max_entries=UWUPLUS_CACHE_MAX_ENTRIES,
            revalidate_seconds=UWUPLUS_REVALIDATE_SECONDS,
        )
        cache.bind_domain(root)
        _UWUPLUS_CACHES[root] = cache
    return cache


def normalize_uwuplus_url(value: str) -> str:
    return normalize_comet_url(value)


class UwuPlusConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = ""

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, value: str) -> str:
        text = value.strip()
        if not text:
            return ""
        return normalize_uwuplus_url(text)


class UwuPlusProvider:
    kind = "source"
    type = "uwuplus"
    capabilities: frozenset[str] = frozenset({"episode"})
    download_rank = 22

    def __init__(self, config: UwuPlusConfig | None = None) -> None:
        self.config = config or UwuPlusConfig()

    @classmethod
    def config_model(cls) -> type[BaseModel]:
        return UwuPlusConfig

    def health(self) -> Health:
        return Health(
            status="ok",
            code=None,
            message="UwU+ sert au téléchargement (liens hébergeurs via AllDebrid).",
            checked_at=datetime.now(timezone.utc),
        )

    @classmethod
    def open_episode(
        cls,
        settings,
        *,
        base_url: str | None = None,
        transport=None,
        request_delay_seconds: float | None = None,
    ):
        if base_url is not None:
            root = base_url.strip().rstrip("/")
        else:
            root = (getattr(settings, "uwuplus_base_url", None) or "").strip().rstrip("/")
        if not root:
            return None
        delay = float(
            settings.source_request_delay_seconds
            if request_delay_seconds is None
            else request_delay_seconds
        )
        return UwuPlusSource(root, delay=delay, transport=transport)

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


class UwuPlusSource:
    """Keyless UwU+ client. JSON responses are cached for about an hour."""

    type = "uwuplus"

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
            raise SourceFetchError("BAD_URL", "L'URL UwU+ est invalide.", retryable=False)
        try:
            validate_outbound_url_static(root)
        except OutboundUrlRejected as exc:
            raise SourceFetchError("BAD_URL", str(exc), retryable=False) from exc
        self.base_url = root
        self.quality_profile = None
        self.last_empty_reason: str | None = None
        self.last_json_from_cache: bool = False
        self.cache = cache if cache is not None else shared_uwuplus_cache(self.base_url)
        self.cache.bind_domain(self.base_url)
        self.pacer = pacer if pacer is not None else shared_comet_pacer(self.base_url, float(delay))
        self._client, self._owns_client = borrow_http_client(
            ("uwuplus", self.base_url),
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
        raw = select_uwu_streams(payload, query, preferences, profile=self.quality_profile)
        if self.quality_profile is not None and not raw:
            from mediatracker.providers.sources.stremio_stream_messages import (
                profile_mismatch_reason,
                stream_rows_seen,
            )

            seen = stream_rows_seen(payload)
            if seen > 0 and _uwu_has_profile_candidates(payload, query):
                self.last_empty_reason = profile_mismatch_reason(self.quality_profile, seen=seen)
            else:
                self.last_empty_reason = "UwU+ n'a aucune source pour ce titre."
        else:
            self.last_empty_reason = None
        return raw

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
        from mediatracker.core.metrics.worker_counters import SOURCE_UWUPLUS, record_source_request
        from mediatracker.security.ssrf import OutboundUrlRejected

        if _SECRET_IN_URL.search(url):
            raise SourceFetchError("BAD_URL", "Requête UwU+ invalide.", retryable=False)
        self.pacer.wait()
        try:
            response = self._client.get(url)
        except OutboundUrlRejected as exc:
            record_source_request(SOURCE_UWUPLUS, error=True)
            raise SourceFetchError("BAD_URL", str(exc), retryable=False) from exc
        except httpx.HTTPError as exc:
            record_source_request(SOURCE_UWUPLUS, error=True)
            raise SourceFetchError("UWUPLUS_DOWN", "UwU+ est injoignable.", retryable=True) from exc
        if response.status_code == 404:
            record_source_request(SOURCE_UWUPLUS, error=False)
            return None, None
        if response.status_code == 403:
            record_source_request(SOURCE_UWUPLUS, error=True)
            raise SourceFetchError(
                "UWUPLUS_FORBIDDEN",
                "UwU+ a refusé la requête.",
                retryable=True,
            )
        if response.status_code != 200:
            record_source_request(SOURCE_UWUPLUS, error=True)
            raise SourceFetchError(
                "UWUPLUS_DOWN",
                f"UwU+ a répondu HTTP {response.status_code}.",
                retryable=response.status_code >= 500,
            )
        record_source_request(SOURCE_UWUPLUS, error=False)
        path = _cache_path_from_url(url, self.base_url)
        return response.text, path


def _uwu_has_profile_candidates(payload: dict[str, Any], query: EpisodeQuery) -> bool:
    rows = payload.get("streams") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return False
    return any(_profile_pair(row, query) is not None for row in rows)


def _is_uwu_info_only_row(row: dict[str, Any]) -> bool:
    if _is_rescrape(row):
        return True
    name = row.get("name") if isinstance(row.get("name"), str) else ""
    title = row.get("title") if isinstance(row.get("title"), str) else ""
    if "aucun flux" in name.casefold():
        return True
    if title.strip().casefold() == "aucune source trouvée":
        return True
    return False


def select_uwu_streams(
    payload: dict[str, Any],
    query: EpisodeQuery,
    preferences: SourcePreferences,
    profile: Profile | None = None,
) -> list[LinkCandidate]:
    if profile is not None:
        return _select_for_profile(payload, query, profile)
    rows = payload.get("streams") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    found: list[LinkCandidate] = []
    seen: set[str] = set()
    for row in rows:
        candidate = _ddl_candidate(row, query, preferences)
        if candidate is None or candidate.external_id in seen:
            continue
        seen.add(candidate.external_id)
        found.append(candidate)
    preference = ensure_1fichier_last(list(DEFAULT_HOSTER_PREFERENCE))
    found.sort(
        key=lambda item: (
            hoster_rank(item.hoster, preference),
            _quality_rank(item.quality),
            -(item.size or 0),
            item.external_id,
        )
    )
    return found


def _select_for_profile(
    payload: dict[str, Any],
    query: EpisodeQuery,
    profile: Profile,
) -> list[LinkCandidate]:
    rows = payload.get("streams") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        logger.info("UwU+ : 0 flux, 0 écarté(s) par le profil « %s », 0 retenu(s).", profile.name)
        return []
    parsed: list[tuple[LinkCandidate, ReleaseCandidate]] = []
    seen: set[str] = set()
    for row in rows:
        pair = _profile_pair(row, query)
        if pair is None:
            continue
        link, candidate = pair
        if link.external_id in seen:
            continue
        seen.add(link.external_id)
        parsed.append((link, candidate))
    kept, dropped = filter_candidates([item[1] for item in parsed], profile)
    logger.info(
        "UwU+ : %s flux, %s écarté(s) par le profil « %s », %s retenu(s).",
        len(parsed),
        dropped,
        profile.name,
        len(kept),
    )
    chosen = [parsed[index][0] for index in kept]
    preference = ensure_1fichier_last(list(DEFAULT_HOSTER_PREFERENCE))
    chosen.sort(
        key=lambda item: (
            hoster_rank(item.hoster, preference),
            _quality_rank(item.quality),
            -(item.size or 0),
            item.external_id,
        )
    )
    return chosen


def _profile_pair(row: object, query: EpisodeQuery) -> tuple[LinkCandidate, ReleaseCandidate] | None:
    if not isinstance(row, dict):
        return None
    if not _is_ddl_row(row):
        return None
    hints = row.get("behaviorHints") if isinstance(row.get("behaviorHints"), dict) else {}
    filename = _release_filename(row, hints)
    if not filename:
        return None
    unlock_url, hoster = _unlock_url_and_hoster(row)
    if unlock_url is None or hoster is None:
        return None
    if hoster_is_blocked(hoster):
        return None
    description = row.get("description") if isinstance(row.get("description"), str) else ""
    name = row.get("name") if isinstance(row.get("name"), str) else ""
    title = row.get("title") if isinstance(row.get("title"), str) else ""
    blob = " ".join(part for part in (filename, description, name, title) if part)
    if _BANNED.search(blob):
        return None
    if not _matches_title(filename, query):
        return None
    if not _matches_slot(filename, query):
        return None
    size = stream_file_size(row, hints, filename, file_index=None)
    language = detect_language(blob)
    resolution = normalize_resolution(f"{filename} {name}")
    external_id = _external_id(unlock_url)
    link = LinkCandidate(
        url=unlock_url,
        kind="ddl_hoster",
        hoster=hoster,
        quality=resolution,
        language=language,
        size=size if isinstance(size, int) else None,
        filename=filename[:500],
        season=query.season,
        episode=query.episode,
        external_id=external_id,
    )
    return link, ReleaseCandidate(
        language=language,
        resolution=resolution,
        size=size if isinstance(size, int) else None,
        text=blob,
    )


def _ddl_candidate(row: object, query: EpisodeQuery, preferences: SourcePreferences) -> LinkCandidate | None:
    pair = _profile_pair(row, query)
    if pair is None:
        return None
    link, _candidate = pair
    language = link.language
    if language is None or not _language_ok(language, preferences):
        return None
    quality = canonical_quality(link.quality or "")
    if not _quality_ok(quality, preferences):
        return None
    link.quality = quality
    return link


def _is_ddl_row(row: dict[str, Any]) -> bool:
    if _is_uwu_info_only_row(row):
        return False
    if _is_web_row(row):
        return False
    info = str(row.get("infoHash") or row.get("info_hash") or row.get("btih") or "").strip()
    if len(info) == 40 and all(ch in "0123456789abcdef" for ch in info.lower()):
        return False
    hints = row.get("behaviorHints") if isinstance(row.get("behaviorHints"), dict) else {}
    filename = _release_filename(row, hints)
    if not filename or _BAD_EXT.search(filename):
        return False
    if not _release_name_complete(filename):
        return False
    unlock_url, hoster = _unlock_url_and_hoster(row)
    if unlock_url is None or hoster is None:
        return False
    if hoster_is_blocked(hoster):
        return False
    stream_url = row.get("url") if isinstance(row.get("url"), str) else ""
    if not stream_url or "resolve-stream" not in urlsplit(stream_url).path:
        return False
    path = urlsplit(stream_url).path.lower()
    if path.endswith(".m3u8"):
        return False
    return True


def _is_rescrape(row: dict[str, Any]) -> bool:
    url = row.get("url") if isinstance(row.get("url"), str) else ""
    if "rescrape" in url.lower() or "/stream-action/" in url.lower():
        return True
    hints = row.get("behaviorHints") if isinstance(row.get("behaviorHints"), dict) else {}
    filename = hints.get("filename") if isinstance(hints.get("filename"), str) else ""
    return filename.casefold() == "rescrape.webm"


def _is_web_row(row: dict[str, Any]) -> bool:
    name = row.get("name") if isinstance(row.get("name"), str) else ""
    if _WEB_NAME.search(name):
        return True
    url = row.get("url") if isinstance(row.get("url"), str) else ""
    path = urlsplit(url).path.lower()
    if "/mp4-proxy/" in path or "/hls-proxy/" in path:
        return True
    if path.endswith(".m3u8"):
        return True
    title = row.get("title") if isinstance(row.get("title"), str) else ""
    if "lecteur" in title.lower():
        return True
    return False


def _release_filename(row: dict[str, Any], hints: dict[str, Any]) -> str | None:
    filename = hints.get("filename") if isinstance(hints.get("filename"), str) else None
    if filename and filename.strip():
        return filename.strip()
    description = row.get("description") if isinstance(row.get("description"), str) else ""
    for line in description.splitlines():
        cleaned = line.replace("📄", "").strip()
        if cleaned:
            return cleaned
    return None


def _release_name_complete(filename: str) -> bool:
    stem = filename.strip()
    if _VIDEO.search(stem):
        return True
    if stem.count(".") >= 2:
        return True
    parsed = parse_release(stem)
    return parsed.year is not None or parsed.resolution is not None


def _unlock_url_and_hoster(row: dict[str, Any]) -> tuple[str | None, str | None]:
    external = row.get("externalUrl")
    unlock = external.strip() if isinstance(external, str) else ""
    if not unlock.lower().startswith(("http://", "https://")):
        url = row.get("url") if isinstance(row.get("url"), str) else ""
        unlock = _decode_wrapped_url(url) or ""
    if not unlock:
        return None, None
    hoster = _hoster_from_row(row)
    if hoster is None:
        return None, None
    return unlock, hoster


def _decode_wrapped_url(url: str) -> str | None:
    if not url:
        return None
    parsed = urlsplit(url)
    if "resolve-stream" not in parsed.path:
        return None
    qs = parse_qs(parsed.query)
    encoded = (qs.get("url") or [None])[0]
    if not encoded:
        return None
    padding = "=" * (-len(encoded) % 4)
    try:
        raw = base64.urlsafe_b64decode(encoded + padding)
    except (ValueError, binascii.Error):
        try:
            raw = base64.b64decode(encoded + padding)
        except (ValueError, binascii.Error):
            return None
    text = raw.decode("utf-8", errors="ignore").strip()
    if text.lower().startswith(("http://", "https://")):
        return text
    return None


def _hoster_from_row(row: dict[str, Any]) -> str | None:
    for field in ("description", "title", "name"):
        text = row.get(field)
        if not isinstance(text, str):
            continue
        match = _HOSTER_LINE.search(text)
        if match is not None:
            mapped = _map_hoster_label(match.group(1))
            if mapped is not None:
                return mapped
    return None


def _map_hoster_label(label: str) -> str | None:
    folded = label.strip().lower().replace(" ", "").replace("-", "")
    if folded in {"1fichier", "1fichiercom"}:
        return "1fichier"
    if folded.startswith("rapidgator"):
        return "rapidgator"
    if folded.startswith("turbobit") or folded.startswith("turbob"):
        return "turbobit"
    if folded.startswith("uploady"):
        return "uploady"
    if folded.startswith("dailyuploads"):
        return "dailyuploads"
    if folded.startswith("nitroflare"):
        return "nitroflare"
    for blocked in BLOCKED_SOURCE_HOSTERS:
        if folded.startswith(blocked):
            return blocked
    if folded in KNOWN_ALIASES:
        return KNOWN_ALIASES[folded]
    return None


KNOWN_ALIASES = {
    "katfile": "katfile",
    "tezfiles": "tezfiles",
}


def _external_id(unlock_url: str) -> str:
    digest = hashlib.sha256(unlock_url.encode("utf-8")).hexdigest()[:24]
    return f"uwuplus:{digest}"


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
        raise SourceFetchError("UWUPLUS_DOWN", "Réponse UwU+ illisible.", retryable=True) from exc
    if not isinstance(payload, dict):
        raise SourceFetchError("UWUPLUS_DOWN", "Réponse UwU+ inattendue.", retryable=True)
    return payload


def _matches_slot(filename: str, query: EpisodeQuery) -> bool:
    if query.media_type == "film":
        season, episode = slot_from_name(filename)
        return season is None and episode is None
    if query.season is None or query.episode is None:
        return False
    return release_matches_episode(filename, season=query.season, episode=query.episode)


def _language_ok(language: str | None, preferences: SourcePreferences) -> bool:
    if language is None:
        return False
    wanted = {preferences.language}
    if preferences.allow_fallback:
        wanted.update(preferences.language_fallback)
    french = frozenset({"VFF", "VF", "VFQ", "MULTI"})
    if language in french and wanted & french:
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


def _quality_rank(quality: str | None) -> int:
    order = {"1080p": 0, "HD": 1, "HDLight": 2, "SD": 3, "4K": 4}
    if quality is None:
        return 9
    return order.get(quality, 8)