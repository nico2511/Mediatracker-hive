"""Wawacity source. Search and episode pages only; dl-protect is left to AllDebrid.

The base URL and the User-Agent come from settings because the domain changes.
Nothing here solves a captcha, and nothing here logs a fetched URL or a body.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import TYPE_CHECKING, cast

import httpx

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
from pydantic import BaseModel, ConfigDict, field_validator

from mediatracker.core.hosters import source_ingestion_hosters
from mediatracker.core.source_select import identity_match, titles_match
from mediatracker.providers.base import (
    Health,
    LinkCandidate,
    MediaType,
    ReleaseListing,
    SourceFetchError,
    SourceItem,
    SourcePreferences,
    SourceQuery,
    SourceRef,
    SourceResult,
    SourceTitle,
)
from mediatracker.providers.sources.throttle import HtmlCache, SourcePacer
from mediatracker.providers.sources.wawacity_html import (
    AlternateQuality,
    PageLink,
    SearchHit,
    parse_alternate_qualities,
    parse_links,
    parse_listing,
    parse_search,
)
from mediatracker.providers.sources.wawacity_query import (
    apply_listing,
    film_discover_urls,
    listing_slug,
    listing_url,
    original_discover_urls,
    plain_search_url,
    search_phrases,
    search_slugs,
    search_url,
)
from mediatracker.security.ssrf import OutboundUrlRejected, validate_outbound_url_static
from mediatracker.security.wawacity_redirect import (
    WawacityRedirectRejected,
    get_following_brand_redirects,
    record_redirect_blocked,
)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_MAX_BODY = 2_000_000
_CHALLENGE = (
    "cf-browser-verification",
    "just a moment",
    "/cdn-cgi/challenge-platform",
    "attention required",
)


class WawacityConfig(BaseModel):
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
            raise ValueError("L'URL Wawacity doit commencer par http:// ou https://.")
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


class WawacitySource:
    """One remote catalogue. ``search`` and ``get_links`` are the only network calls."""

    kind = "source"
    type = "wawacity"
    capabilities = frozenset({"search", "items", "links", "latest"})
    download_rank = 10

    def __init__(
        self,
        config: WawacityConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        pacer: SourcePacer | None = None,
        cache: HtmlCache | None = None,
        timeout: float = 20.0,
        redirect_db: Session | None = None,
        redirect_settings=None,
    ) -> None:
        from mediatracker.core.outbound_http import borrow_http_client

        self.config = config
        self.pacer = pacer
        self.cache = cache
        self._redirect_db = redirect_db
        self._redirect_settings = redirect_settings
        self._transport = transport
        key = ("wawacity", config.base_url, config.user_agent)
        from mediatracker.security.ssrf import httpx_request_event_hooks

        self._client, self._owns_client = borrow_http_client(
            key,
            httpx.Client,
            transport=transport,
            timeout=timeout,
            follow_redirects=False,
            event_hooks=httpx_request_event_hooks(),
            headers={
                "User-Agent": config.user_agent,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "fr-FR,fr;q=0.9",
            },
        )

    @classmethod
    # Quoted: the class attribute ``type`` would shadow the builtin in this body.
    def config_model(cls) -> type[BaseModel]:
        return WawacityConfig

    @classmethod
    def open_poll(cls, db, settings, *, pacer=None, cache=None, transport=None, redirect_db=None):
        """Open a poll client from stored settings. The pacer and cache are shared by the caller."""
        from mediatracker.db.models import Setting
        from mediatracker.providers.sources.loading import source_base_url

        def text(key: str) -> str:
            row = db.get(Setting, key)
            if row is None or not row.value:
                return ""
            return row.value

        base = source_base_url(db, settings, cls.type)
        agent = text("wawacity_user_agent") or (settings.wawacity_user_agent or "") or DEFAULT_USER_AGENT
        config = WawacityConfig(base_url=base, user_agent=agent)
        if not config.base_url:
            return None
        observe_db = redirect_db if redirect_db is not None else db
        return cls(
            config,
            transport=transport,
            pacer=pacer,
            cache=cache,
            redirect_db=observe_db,
            redirect_settings=settings,
        )

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
                message="L'URL Wawacity n'est pas configurée.",
                checked_at=checked,
            )
        try:
            self._get(self.config.base_url + "/")
        except SourceFetchError as exc:
            status = "degraded" if exc.challenge else "down"
            return Health(status=status, code=exc.code, message=exc.message, checked_at=checked)
        return Health(status="ok", code=None, message="Wawacity répond.", checked_at=checked)

    def latest_releases(
        self,
        media_type: MediaType,
        quality: str | None = None,
        language: str | None = None,
    ) -> list[ReleaseListing]:
        """One versioned listing. Series default to VF HD, films to Blu-ray 1080p/720p."""
        self._require_base()
        kind = media_type if media_type in {"film", "series"} else "film"
        slug = listing_slug(kind, quality or "HD", language or "VFF")
        url = listing_url(self.config.base_url, kind, slug)
        html = self._get(url)
        hits = []
        for hit in parse_listing(html, self.config.base_url, kind):
            if hit.rejection:
                continue
            quality_name, language_name = apply_listing(hit.quality, hit.language, slug, media_type=kind)
            hits.append(
                SearchHit(
                    title=hit.title,
                    year=hit.year,
                    media_type=hit.media_type,
                    season=hit.season,
                    quality=quality_name,
                    language=language_name,
                    url=hit.url,
                    episode=hit.episode,
                    poster_url=hit.poster_url,
                )
            )
        return [_listing(hit, kind) for hit in hits]

    def search(self, query: str, media_type: MediaType | None) -> list[SourceResult]:
        self._require_base()
        kind = "film" if media_type == "film" else "series"
        if kind == "film":
            return self._search_plain(query, kind, None)
        preferences = SourcePreferences()
        slug = search_slugs(kind, preferences)[0]
        return self._search_slug(query, kind, slug)

    def discover(self, query: SourceQuery, preferences: SourcePreferences) -> list[SourceResult]:
        """Preferred version first, then the fallbacks the preferences allow.

        Each phrase is fetched at most once per listing. The HTML cache and the
        pacer sit under ``_get``. Cam and TS listings are never requested.
        """
        self._require_base()
        kind = query.media_type if query.media_type in {"film", "series"} else "series"
        if kind == "film":
            return self._discover_film(query)
        phrases = search_phrases(query.title, query.original_title, query.year)
        found: list[SourceResult] = []
        seen: set[str] = set()
        for slug in search_slugs(kind, preferences):
            for phrase in phrases:
                for item in self._search_slug(phrase, kind, slug):
                    if item.url and item.url in seen:
                        continue
                    if item.url:
                        seen.add(item.url)
                    found.append(item)
                if _phrase_hit(found, query.title, query.original_title):
                    break
            if _seasons_covered(found, query):
                break
        return found

    def _discover_film(self, query: SourceQuery) -> list[SourceResult]:
        found: list[SourceResult] = []
        seen: set[str] = set()

        def absorb(batch: list[SourceResult]) -> None:
            for item in batch:
                if not identity_match(
                    item.title,
                    item.year,
                    title=query.title,
                    original_title=query.original_title,
                    year=query.year,
                    media_type="film",
                    candidate_ref=item.url or item.ref,
                ):
                    continue
                if item.rejection:
                    continue
                if item.url and item.url in seen:
                    continue
                if item.url:
                    seen.add(item.url)
                found.append(item)

        for phrase, year in film_discover_urls(query.title, query.original_title, query.year):
            absorb(self._search_plain(phrase, "film", year))
            if _seasons_covered(found, query):
                return found

        if not found:
            for phrase, year in original_discover_urls(query.original_title, query.year):
                absorb(self._search_plain(phrase, "film", year))
                if _seasons_covered(found, query):
                    break
        return found

    def _search_slug(self, query: str, media_type: str, slug: str) -> list[SourceResult]:
        url = search_url(self.config.base_url, media_type, query, slug)
        html = self._get(url)
        return self._parse_search_page(html, media_type, slug=slug)

    def _search_plain(self, query: str, media_type: str, year: int | None) -> list[SourceResult]:
        url = plain_search_url(self.config.base_url, media_type, query, year=year)
        html = self._get(url)
        return self._parse_search_page(html, media_type, slug=None)

    def _parse_search_page(self, html: str, media_type: str, *, slug: str | None) -> list[SourceResult]:
        results: list[SourceResult] = []
        for hit in parse_search(html, self.config.base_url):
            quality, language = hit.quality, hit.language
            if slug is not None:
                quality, language = apply_listing(hit.quality, hit.language, slug, media_type=media_type)
            results.append(
                _result(
                    SearchHit(
                        title=hit.title or "",
                        year=hit.year,
                        media_type=hit.media_type or media_type,
                        season=hit.season,
                        quality=quality,
                        language=language,
                        url=hit.url,
                        rejection=hit.rejection,
                        profile_hint=hit.profile_hint,
                    )
                )
            )
        return results

    def get_title(self, ref: SourceRef) -> SourceTitle:
        heading, _links, _alternates = self._page(ref)
        return SourceTitle(
            title=heading.title or ref.ref,
            year=heading.year,
            media_type=heading.media_type or "series",
            variants=[],
        )

    def list_items(self, ref: SourceRef) -> list[SourceItem]:
        _heading, links, _alternates = self._page(ref)
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
        profile=None,
        hoster_preference: Sequence[str] | None = None,
    ) -> list[LinkCandidate]:
        keep = source_ingestion_hosters(hoster_preference)
        keep_set = set(keep)
        heading, links, alternates = self._page(ref)
        if profile is not None and heading.media_type in {None, "film"}:
            sister = _sister_heading_for_profile(heading, alternates, profile)
            if sister is not None:
                sister_ref = SourceRef(ref=sister.url, url=sister.url)
                heading, links, _alts = self._page(sister_ref)
        candidates = [
            _candidate(link, heading.quality, heading.language)
            for link in links
            if link.hoster in keep_set
        ]
        candidates.sort(
            key=lambda item: (item.episode or 0, keep.index(item.hoster or ""), item.url),
        )
        return candidates

    def _page(self, ref: SourceRef):
        self._require_base()
        url = (ref.url or ref.ref).strip()
        if not url:
            raise SourceFetchError("EMPTY_REF", "Page source sans URL.", retryable=False)
        html = self._get(url)
        heading, links = parse_links(html, url)
        alternates = parse_alternate_qualities(
            html,
            self.config.base_url,
            page_title=heading.title,
            page_year=heading.year,
        )
        return heading, links, alternates

    def _require_base(self) -> None:
        if not self.config.base_url:
            raise SourceFetchError(
                "NOT_CONFIGURED",
                "L'URL Wawacity n'est pas configurée.",
                retryable=False,
            )

    def _get(self, url: str) -> str:
        if self.cache is not None:
            cached = self.cache.get(url)
            if cached is not None:
                return cached
        if self.pacer is not None:
            self.pacer.wait()
        from mediatracker.core.metrics.worker_counters import SOURCE_WAWACITY, record_source_request

        try:
            response = get_following_brand_redirects(
                self._client,
                url,
                configured_base=self.config.base_url,
                max_redirects=5,
            )
        except WawacityRedirectRejected as exc:
            record_source_request(SOURCE_WAWACITY, error=True)
            if self._redirect_db is not None:
                record_redirect_blocked(self._redirect_db, target_host=exc.target_host)
            raise SourceFetchError("REDIRECT_BLOCKED", str(exc), retryable=False) from exc
        except OutboundUrlRejected as exc:
            record_source_request(SOURCE_WAWACITY, error=True)
            raise SourceFetchError("POLICY", str(exc), retryable=False) from exc
        except httpx.TransportError as exc:
            record_source_request(SOURCE_WAWACITY, error=True)
            raise SourceFetchError("TRANSPORT", "Wawacity est injoignable.", retryable=True) from exc
        if len(response.content) > _MAX_BODY:
            raise SourceFetchError("TOO_LARGE", "La page Wawacity est trop volumineuse.", retryable=True)
        body = response.text
        if _challenge(response.status_code, response.headers, body):
            record_source_request(SOURCE_WAWACITY, error=True)
            code = "HTTP_429" if response.status_code == 429 else "CLOUDFLARE"
            message = (
                "Wawacity limite les requêtes (429). Prochain essai repoussé."
                if code == "HTTP_429"
                else "Wawacity a renvoyé un contrôle Cloudflare. Prochain essai repoussé."
            )
            raise SourceFetchError(code, message, retryable=True, challenge=True)
        if response.status_code >= 400:
            record_source_request(SOURCE_WAWACITY, error=True)
            raise SourceFetchError(
                f"HTTP_{response.status_code}",
                f"Wawacity a répondu HTTP {response.status_code}.",
                retryable=response.status_code >= 500 or response.status_code == 429,
            )
        record_source_request(SOURCE_WAWACITY, error=False)
        if self._redirect_db is not None and self._redirect_settings is not None:
            from mediatracker.core.wawacity_domain import handle_domain_from_redirect

            handle_domain_from_redirect(
                self._redirect_db,
                self._redirect_settings,
                url,
                response,
                configured_base=self.config.base_url,
                transport=self._transport,
            )
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


def _listing(hit: SearchHit, media_type: MediaType) -> ReleaseListing:
    kind = hit.media_type if hit.media_type in {"film", "series"} else media_type
    return ReleaseListing(
        title=hit.title,
        year=hit.year,
        media_type=cast(MediaType, kind),
        season=hit.season,
        episode=hit.episode,
        quality=hit.quality,
        language=hit.language,
        url=hit.url,
        ref=hit.url,
        source_poster_url=hit.poster_url,
    )


def _result(hit: SearchHit) -> SourceResult:
    return SourceResult(
        title=hit.title,
        year=hit.year,
        media_type=hit.media_type if hit.media_type in {"film", "series"} else None,
        ref=hit.url,
        url=hit.url,
        season=hit.season,
        quality=hit.quality,
        language=hit.language,
        profile_hint=hit.profile_hint,
        rejection=hit.rejection,
    )


def _phrase_hit(results: list[SourceResult], title: str, original: str | None) -> bool:
    names = [name for name in (title, original or "") if name.strip()]
    return any(any(titles_match(item.title, name) for name in names) for item in results)


def _release_candidate_from_heading(heading):
    from mediatracker.core.quality_profile import release_candidate_from_fields

    return release_candidate_from_fields(
        language=heading.language,
        quality=heading.quality,
        hint=getattr(heading, "profile_hint", ""),
        title=heading.title,
    )


def _release_candidate_from_alternate(alt: AlternateQuality):
    from mediatracker.core.quality_profile import release_candidate_from_fields

    return release_candidate_from_fields(
        language=alt.language,
        quality=alt.quality,
        hint=alt.label,
        title=alt.title,
    )


def _heading_acceptance_rank(heading, profile) -> int | None:
    from mediatracker.core.quality_profile import acceptance_rank

    if profile is None:
        return None
    return acceptance_rank(_release_candidate_from_heading(heading), profile)


def _heading_matches_profile(heading, profile) -> bool:
    if profile is None:
        return True
    return _heading_acceptance_rank(heading, profile) is not None


def _pick_alternate_heading(alternates: list[AlternateQuality], profile) -> AlternateQuality | None:
    from mediatracker.core.quality_profile import acceptance_rank

    if not alternates:
        return None
    ranked: list[tuple[int, AlternateQuality]] = []
    for alt in alternates:
        if alt.rejection:
            continue
        penalty = acceptance_rank(_release_candidate_from_alternate(alt), profile)
        if penalty is None:
            continue
        ranked.append((penalty, alt))
    if not ranked:
        return None
    ranked.sort(key=lambda pair: pair[0])
    return ranked[0][1]


def _sister_heading_for_profile(heading, alternates: list[AlternateQuality], profile) -> AlternateQuality | None:
    linked_rank = _heading_acceptance_rank(heading, profile)
    best = _pick_alternate_heading(alternates, profile)
    if best is None:
        return None
    if linked_rank is None:
        return best
    from mediatracker.core.quality_profile import acceptance_rank

    sister_rank = acceptance_rank(_release_candidate_from_alternate(best), profile)
    if sister_rank is not None and sister_rank < linked_rank:
        return best
    return None


def _seasons_covered(results: list[SourceResult], query: SourceQuery) -> bool:
    if query.media_type == "film":
        return any(
            identity_match(
                item.title,
                item.year,
                title=query.title,
                original_title=query.original_title,
                year=query.year,
                media_type="film",
                candidate_ref=item.url or item.ref,
            )
            and not item.rejection
            for item in results
        )
    if not query.seasons:
        return False
    found = {
        item.season
        for item in results
        if item.season is not None and titles_match(item.title, query.title) and not item.rejection
    }
    return set(query.seasons).issubset(found)


def _candidate(link: PageLink, quality: str | None, language: str | None) -> LinkCandidate:
    from mediatracker.core.quality_profile import normalize_resolution

    digest = hashlib.sha256(link.url.encode("utf-8")).hexdigest()
    picked_quality = link.quality or quality
    page_hd_label = (
        not (link.filename or "").strip()
        and normalize_resolution(picked_quality) == "HD"
    )
    return LinkCandidate(
        url=link.url,
        kind="ddl_protected",
        hoster=link.hoster,
        quality=picked_quality,
        language=link.language or language,
        season=link.season,
        episode=link.episode,
        filename=link.filename,
        page_hd_label=page_hd_label,
        external_id=f"wawacity:{digest[:32]}",
    )
