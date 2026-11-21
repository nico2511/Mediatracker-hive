"""Provider protocols and the DTOs shared by the API, the plugins, and the tests.

A source speaks this neutral model and nothing site-specific:

- media type: ``film`` or ``series``
- language: VF, VFF, VFQ, MULTI, VOSTFR, VO
- quality: SD, HD, 720p, 1080p, 4K, HDLight
- season and episode
- user preferences (``SourcePreferences``), including whether a fallback is allowed

``search`` is a plain text query. ``discover`` is optional: the core passes a
``SourceQuery`` and ``SourcePreferences``, and the source decides which URLs
to open, which of its own filters apply, and how to map the pages back onto
``SourceResult``. Another site can implement ``discover`` without a change in
the poller, the fiche, or the UI. Wawacity's ``s=`` codes stay in
``providers/sources/wawacity_query.py``.
"""

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, Field

LinkKind = Literal["ddl_protected", "ddl_hoster", "direct", "magnet", "torrent"]
MediaType = Literal["film", "series"]
HealthState = Literal["ok", "degraded", "down"]


class Health(BaseModel):
    status: HealthState
    message: str | None = None
    code: str | None = None
    checked_at: datetime


class LinkCandidate(BaseModel):
    url: str
    kind: LinkKind
    hoster: str | None = None
    quality: str | None = None
    language: str | None = None
    size: int | None = None
    filename: str | None = None
    season: int | None = None
    episode: int | None = None
    # Torrent file index, when the source names one file inside the magnet.
    file_index: int | None = None
    external_id: str
    # Generic Wawacity page heading « HD » only (no release name on the row).
    page_hd_label: bool = False


class SourceResult(BaseModel):
    title: str
    year: int | None = None
    media_type: MediaType | None = None
    ref: str
    url: str | None = None
    season: int | None = None
    quality: str | None = None
    language: str | None = None
    # Raw listing or search anchor text when the parsed title dropped a language variant.
    profile_hint: str = ""
    # Set by the source when it already knows the page must be refused.
    rejection: str | None = None


class SourcePreferences(BaseModel):
    """User quality and language. The source maps them onto its own filters."""

    quality: str = "HD"
    language: str = "VFF"
    allow_fallback: bool = False
    quality_fallback: list[str] = Field(default_factory=list)
    language_fallback: list[str] = Field(default_factory=list)


class SourceQuery(BaseModel):
    """One fiche to look up. Titles are tried by the source, not by the core."""

    title: str
    original_title: str | None = None
    year: int | None = None
    media_type: MediaType
    seasons: list[int] = Field(default_factory=list)


class EpisodeQuery(BaseModel):
    """One episode or film to download. Catalog sources do not use this."""

    title: str
    original_title: str | None = None
    year: int | None = None
    media_type: MediaType
    imdb_id: str | None = None
    season: int | None = None
    episode: int | None = None
    # Wawacity release names. Comet prefers a cached stream that matches one.
    release_names: list[str] = Field(default_factory=list)


class ReleaseListing(BaseModel):
    """One row from a source's latest-releases page. Several sources can describe the same title."""

    title: str
    year: int | None = None
    media_type: MediaType
    season: int | None = None
    episode: int | None = None
    quality: str | None = None
    language: str | None = None
    url: str
    ref: str
    source_poster_url: str | None = None


class SourceFetchError(Exception):
    """A source HTTP call failed. The message is safe to store: no secrets, no CDN URL."""

    def __init__(self, code: str, message: str, *, retryable: bool = True, challenge: bool = False) -> None:
        self.code = code
        self.message = message
        self.retryable = retryable
        self.challenge = challenge
        super().__init__(message)


class SourceRef(BaseModel):
    ref: str
    url: str | None = None


class SourceTitle(BaseModel):
    title: str
    year: int | None = None
    media_type: MediaType
    variants: list[SourceRef] = Field(default_factory=list)


class SourceItem(BaseModel):
    season: int | None = None
    episode: int | None = None
    title: str | None = None
    ref: str


class DirectLink(BaseModel):
    url: str
    filename: str | None = None
    size: int | None = None
    expires_at: datetime | None = None
    host: str | None = None


class DownloadState(StrEnum):
    pending = "pending"
    resolving = "resolving"
    queued = "queued"
    downloading = "downloading"
    post_processing = "post_processing"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"


class DownloadStatus(BaseModel):
    state: str
    done_bytes: int = 0
    total_bytes: int | None = None
    speed: int | None = None
    error: str | None = None
    path: str | None = None


class CapabilityNotSupported(Exception):
    def __init__(self, provider: str, capability: str) -> None:
        self.provider = provider
        self.capability = capability
        super().__init__(f"{provider} ne prend pas en charge « {capability} ».")


class DebridError(Exception):
    """Neutral debrid failure. Site clients subclass this; the pipeline does not import them."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(message)


AUTH_BLOCKED_MESSAGE = (
    "Nouvelle adresse IP bloquée par AllDebrid (AUTH_BLOCKED). "
    "Confirmez cet accès depuis l'e-mail envoyé par AllDebrid, puis relancez le contrôle de santé."
)


class Source(Protocol):
    kind: str
    type: str
    capabilities: frozenset[str]

    def search(self, query: str, media_type: MediaType | None) -> list[SourceResult]: ...

    def discover(self, query: SourceQuery, preferences: SourcePreferences) -> list[SourceResult]:
        """Map preferences onto this site and return neutral results. Optional."""
        ...

    def get_title(self, ref: SourceRef) -> SourceTitle: ...

    def list_items(self, ref: SourceRef) -> list[SourceItem]: ...

    def get_links(self, ref: SourceRef) -> list[LinkCandidate]: ...

    def latest_releases(self, media_type: MediaType) -> list[ReleaseListing]: ...

    def health(self) -> Health: ...


class EpisodeSource(Protocol):
    """A download source. It does not publish a latest-releases page.

    Wawacity remains the catalog. Another module can offer links for one
    episode through ``links_for`` without the poller or the nouveautés page
    knowing the site.
    """

    type: str

    def links_for(self, query: EpisodeQuery, preferences: SourcePreferences) -> list[LinkCandidate]: ...


class Debrid(Protocol):
    kind: str
    type: str
    capabilities: frozenset[str]

    def supports(self, link: LinkCandidate) -> bool: ...

    def resolve(self, url: str) -> list[str]: ...

    def unlock(self, url: str) -> DirectLink: ...

    def health(self) -> Health: ...


class Downloader(Protocol):
    kind: str
    type: str
    capabilities: frozenset[str]

    def add(
        self,
        url: str,
        dest_dir: Path,
        filename: str | None,
        headers: dict[str, str] | None = None,
    ) -> str: ...

    def status(self, handle: str) -> DownloadStatus: ...

    def pause(self, handle: str) -> None: ...

    def resume(self, handle: str) -> None: ...

    def remove(self, handle: str, delete_files: bool = False) -> None: ...

    def health(self) -> Health: ...
