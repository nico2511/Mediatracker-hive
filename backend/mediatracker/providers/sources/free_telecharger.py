"""Free-Telecharger source. Search and link pages; pacing and cache match Wawacity."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import cast
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, field_validator

from mediatracker.core.hosters import source_ingestion_hosters
from mediatracker.core.source_select import identity_match, titles_match
from mediatracker.providers.base import (
    Health,
    LinkCandidate,
    MediaType,
    SourceFetchError,
    SourceItem,
    SourcePreferences,
    SourceQuery,
    SourceRef,
    SourceResult,
    SourceTitle,
)
from mediatracker.providers.sources.free_telecharger_html import (
    PageLink,
    SearchHit,
    map_media_quality,
    parse_intermediate,
    parse_links,
    parse_search,
)
from mediatracker.providers.sources.free_telecharger_query import search_phrases, search_url
from mediatracker.providers.sources.throttle import HtmlCache, SourcePacer
from mediatracker.providers.sources.wawacity import DEFAULT_USER_AGENT
from mediatracker.security.ssrf import OutboundUrlRejected, validate_outbound_url_static

logger = logging.getLogger(__name__)

_MAX_BODY = 2_000_000
_CHALLENGE = (
    "cf-browser-verification",
    "just a moment",
    "/cdn-cgi/challenge-platform",
    "attention required",
)


class FreeTelechargerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = ""
    user_agent: str = DEFAULT_USER_AGENT

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, value: str) -> str:
        text = value.strip().rstrip("/")
        if not text:
            return ""
        if not (text.startswith("http://") or text.startswith("https://")):
            raise ValueError("L'URL Free-Telecharger doit commencer par http:// ou https://.")
        try:
            validate_outbound_url_static(text)
        except OutboundUrlRejected as exc:
            raise ValueError(str(exc)) from exc
        return text

    @field_validator("user_agent")
    @classmethod
    def _user_agent(cls, value: str) -> str:
        text = " ".join(value.split())
        if not text:
            return DEFAULT_USER_AGENT
        if len(text) > 300:
            raise ValueError("User-Agent trop long.")
        return text


class FreeTelechargerSource:
    """Catalogue Free-Telecharger avec recherche et pages de liens."""

    kind = "source"
    type = "free_telecharger"
    capabilities = frozenset({"search", "items", "links"})
    download_rank = 12

    def __init__(
        self,
        config: FreeTelechargerConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        pacer: SourcePacer | None = None,
        cache: HtmlCache | None = None,
        timeout: float = 20.0,
    ) -> None:
        from mediatracker.core.outbound_http import borrow_http_client

        self.config = config
        self.pacer = pacer
        self.cache = cache
        self._transport = transport
        key = ("free_telecharger", config.base_url, config.user_agent)
        from mediatracker.security.ssrf import httpx_request_event_hooks

        self._client, self._owns_client = borrow_http_client(
            key,
            httpx.Client,
            transport=transport,
            timeout=timeout,
            follow_redirects=True,
            event_hooks=httpx_request_event_hooks(),
            headers={
                "User-Agent": config.user_agent,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "fr-FR,fr;q=0.9",
            },
        )

    @classmethod
    def config_model(cls) -> type[BaseModel]:
        return FreeTelechargerConfig

    @classmethod
    def open_poll(cls, db, settings, *, pacer=None, cache=None, transport=None, redirect_db=None):
        del redirect_db
        from mediatracker.db.models import Setting
        from mediatracker.providers.sources.loading import source_base_url

        def text(key: str) -> str:
            row = db.get(Setting, key)
            if row is None or not row.value:
                return ""
            return row.value

        base = source_base_url(db, settings, cls.type)
        agent = text("free_telecharger_user_agent") or DEFAULT_USER_AGENT
        config = FreeTelechargerConfig(base_url=base, user_agent=agent)
        if not config.base_url:
            return None
        return cls(config, transport=transport, pacer=pacer, cache=cache)

    @classmethod
    def open_cached(cls, db, settings, *, pacer=None, cache=None, transport=None):
        return cls.open_poll(db, settings, pacer=pacer, cache=cache, transport=transport)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def health(self) -> Health:
        checked = datetime.now(timezone.utc)
        if not self.config.base_url:
            return Health(
                status="down",
                code="NOT_CONFIGURED",
                message="L'URL Free-Telecharger n'est pas configurée.",
                checked_at=checked,
            )
        try:
            self._get(self.config.base_url + "/")
        except SourceFetchError as exc:
            status = "degraded" if exc.challenge else "down"
            return Health(status=status, code=exc.code, message=exc.message, checked_at=checked)
        return Health(status="ok", code=None, message="Free-Telecharger répond.", checked_at=checked)

    def search(self, query: str, media_type: MediaType | None) -> list[SourceResult]:
        self._require_base()
        kind = "film" if media_type == "film" else "series"
        return self._search_phrase(query, None, kind)

    def discover(self, query: SourceQuery, preferences: SourcePreferences) -> list[SourceResult]:
        self._require_base()
        kind = query.media_type if query.media_type in {"film", "series"} else "series"
        found: list[SourceResult] = []
        seen: set[str] = set()
        for phrase, year in search_phrases(query.title, query.original_title, query.year):
            for item in self._search_phrase(phrase, year, kind):
                if not identity_match(
                    item.title,
                    item.year,
                    title=query.title,
                    original_title=query.original_title,
                    year=query.year,
                    media_type=kind,
                    candidate_ref=item.url or item.ref,
                ):
                    continue
                if item.rejection:
                    continue
                if not _matches_preferences(item, preferences):
                    continue
                if item.url and item.url in seen:
                    continue
                if item.url:
                    seen.add(item.url)
                found.append(item)
            if _seasons_covered(found, query):
                break
        return found

    def _search_phrase(self, query: str, year: int | None, media_type: str) -> list[SourceResult]:
        url = search_url(self.config.base_url, query, year)
        html = self._get(url)
        results: list[SourceResult] = []
        for hit in parse_search(html, self.config.base_url, want_type=media_type):
            results.append(_result(hit, media_type))
        return results

    def get_title(self, ref: SourceRef) -> SourceTitle:
        heading, _links = self._page(ref)
        return SourceTitle(
            title=heading.title or ref.ref,
            year=heading.year,
            media_type=heading.media_type or "series",
            variants=[],
        )

    def list_items(self, ref: SourceRef) -> list[SourceItem]:
        _heading, links = self._page(ref)
        seen: set[tuple[int | None, int | None]] = set()
        items: list[SourceItem] = []
        for link in links:
            key = (link.season, link.episode)
            if key in seen:
                continue
            seen.add(key)
            items.append(SourceItem(season=link.season, episode=link.episode, title=None, ref=ref.ref))
        return items

    def get_links(
        self,
        ref: SourceRef,
        *,
        hoster_preference: Sequence[str] | None = None,
    ) -> list[LinkCandidate]:
        keep = source_ingestion_hosters(hoster_preference)
        keep_set = set(keep)
        heading, links = self._page(ref)
        resolved: list[PageLink] = []
        for link in links:
            resolved.extend(self._resolve_page_link(link, keep_set=keep_set))
        candidates = [
            _candidate(link, heading.quality, heading.language)
            for link in resolved
            if link.hoster in keep_set
        ]
        candidates.sort(key=lambda item: (item.episode or 0, keep.index(item.hoster or ""), item.url))
        return candidates

    def _resolve_page_link(
        self,
        link: PageLink,
        *,
        keep_set: set[str],
    ) -> list[PageLink]:
        if not link.intermediate:
            return [link]
        html = self._get(link.url, referer=self.config.base_url + "/")
        found: list[PageLink] = []
        for url, hoster in parse_intermediate(html, self.config.base_url):
            if hoster not in keep_set and hoster != "unknown":
                continue
            found.append(
                PageLink(
                    url=url,
                    hoster=hoster if hoster != "unknown" else link.hoster,
                    season=link.season,
                    episode=link.episode,
                    quality=link.quality,
                    language=link.language,
                )
            )
        return found or []

    def _page(self, ref: SourceRef):
        self._require_base()
        url = (ref.url or ref.ref).strip()
        if not url:
            raise SourceFetchError("EMPTY_REF", "Page source sans URL.", retryable=False)
        html = self._get(url)
        return parse_links(html, url, base_url=self.config.base_url)

    def _require_base(self) -> None:
        if not self.config.base_url:
            raise SourceFetchError(
                "NOT_CONFIGURED",
                "L'URL Free-Telecharger n'est pas configurée.",
                retryable=False,
            )

    def _get(self, url: str, *, referer: str | None = None) -> str:
        if self.cache is not None:
            cached = self.cache.get(url)
            if cached is not None:
                return cached
        if self.pacer is not None:
            self.pacer.wait()
        from mediatracker.core.metrics.worker_counters import SOURCE_FREE_TELECHARGER, record_source_request

        headers = {}
        if referer:
            headers["Referer"] = referer
        try:
            response = self._client.get(url, headers=headers or None)
        except OutboundUrlRejected as exc:
            record_source_request(SOURCE_FREE_TELECHARGER, error=True)
            raise SourceFetchError("POLICY", str(exc), retryable=False) from exc
        except httpx.TransportError as exc:
            record_source_request(SOURCE_FREE_TELECHARGER, error=True)
            raise SourceFetchError("TRANSPORT", "Free-Telecharger est injoignable.", retryable=True) from exc
        if len(response.content) > _MAX_BODY:
            raise SourceFetchError("TOO_LARGE", "La page est trop volumineuse.", retryable=True)
        body = response.text
        if _challenge(response.status_code, response.headers, body):
            record_source_request(SOURCE_FREE_TELECHARGER, error=True)
            code = "HTTP_429" if response.status_code == 429 else "CLOUDFLARE"
            message = (
                "Free-Telecharger limite les requêtes (429)."
                if code == "HTTP_429"
                else "Free-Telecharger a renvoyé un contrôle anti-bot."
            )
            raise SourceFetchError(code, message, retryable=True, challenge=True)
        if response.status_code >= 400:
            record_source_request(SOURCE_FREE_TELECHARGER, error=True)
            raise SourceFetchError(
                f"HTTP_{response.status_code}",
                f"Free-Telecharger a répondu HTTP {response.status_code}.",
                retryable=response.status_code >= 500 or response.status_code == 429,
            )
        record_source_request(SOURCE_FREE_TELECHARGER, error=False)
        if self.cache is not None:
            self.cache.set(url, body)
        return body


def _challenge(status: int, headers: httpx.Headers, body: str) -> bool:
    if status == 429:
        return True
    sample = body[:2000].lower()
    if any(marker in sample for marker in _CHALLENGE):
        return True
    server = (headers.get("server") or "").lower()
    cloudflare = "cloudflare" in server or "cf-ray" in {name.lower() for name in headers}
    return status in {403, 503} and cloudflare


def _result(hit: SearchHit, media_type: str) -> SourceResult:
    quality = map_media_quality(hit.quality)
    kind = hit.media_type if hit.media_type in {"film", "series"} else media_type
    return SourceResult(
        title=hit.title,
        year=hit.year,
        media_type=cast(MediaType, kind) if kind in {"film", "series"} else None,
        ref=hit.url,
        url=hit.url,
        season=hit.season,
        quality=quality,
        language=hit.language,
        rejection=hit.rejection,
    )


def _matches_preferences(item: SourceResult, preferences: SourcePreferences) -> bool:
    want_q = preferences.quality or "HD"
    want_l = preferences.language or "VFF"
    if item.quality and item.quality == want_q and item.language and item.language == want_l:
        return True
    if not preferences.allow_fallback:
        return False
    q_ok = item.quality in set(preferences.quality_fallback or ()) or item.quality == want_q
    l_ok = item.language in set(preferences.language_fallback or ()) or item.language == want_l
    return q_ok and l_ok


def _seasons_covered(results: list[SourceResult], query: SourceQuery) -> bool:
    if query.media_type == "film":
        return any(titles_match(item.title, query.title) and not item.rejection for item in results)
    if not query.seasons:
        return False
    found = {
        item.season
        for item in results
        if item.season is not None and titles_match(item.title, query.title) and not item.rejection
    }
    return set(query.seasons).issubset(found)


def _candidate(link: PageLink, quality: str | None, language: str | None) -> LinkCandidate:
    host = (urlparse(link.url).hostname or "").lower()
    digest = hashlib.sha256(link.url.encode("utf-8")).hexdigest()
    return LinkCandidate(
        url=link.url,
        kind="ddl_protected",
        hoster=link.hoster,
        quality=link.quality or quality,
        language=link.language or language,
        season=link.season,
        episode=link.episode,
        filename=link.filename,
        external_id=f"free_telecharger:{host}:{digest[:24]}",
    )
