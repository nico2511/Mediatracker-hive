"""Built-in source: paste a URL, detect the link kind. No remote scraping."""

import hashlib
from datetime import datetime, timezone
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict

from mediatracker.providers.base import (
    CapabilityNotSupported,
    Health,
    LinkCandidate,
    LinkKind,
    MediaType,
    ReleaseListing,
    SourceItem,
    SourceRef,
    SourceResult,
    SourceTitle,
)

# Host suffixes checked against the URL hostname. Uptobox stays blocked at unlock time.
_HOSTERS: tuple[tuple[str, str], ...] = (
    ("rapidgator.net", "rapidgator"),
    ("rapidgator.asia", "rapidgator"),
    ("rg.to", "rapidgator"),
    ("turbobit.net", "turbobit"),
    ("turbobit.cloud", "turbobit"),
    ("turbobit.cc", "turbobit"),
    ("turbo.to", "turbobit"),
    ("turb.cc", "turbobit"),
    ("turb.pw", "turbobit"),
    ("trbbt.net", "turbobit"),
    ("trbt.cc", "turbobit"),
    ("wayupload.com", "turbobit"),
    ("htfl.net", "turbobit"),
    ("hitf.cc", "turbobit"),
    ("hitf.to", "turbobit"),
    ("uploady.io", "uploady"),
    ("dailyuploads.net", "dailyuploads"),
    ("nitroflare.com", "nitroflare"),
    ("katfile.com", "katfile"),
    ("katfile.cloud", "katfile"),
    ("tezfiles.com", "tezfiles"),
    ("1fichier.com", "1fichier"),
    ("ddownload.com", "ddownload"),
    ("uploaded.net", "uploaded"),
    ("uptobox.com", "uptobox"),
)

_PROTECTORS = (
    "dl-protect.link",
    "dl-protect.net",
    "dlprotect.link",
    "protect-link.",
)


class ManualConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ManualSource:
    kind = "source"
    type = "manual"
    capabilities: frozenset[str] = frozenset()

    def __init__(self, config: ManualConfig | None = None) -> None:
        self.config = config or ManualConfig()

    @classmethod
    # Quoted: the class attribute ``type`` would shadow the builtin in this body.
    def config_model(cls) -> "type[BaseModel]":
        return ManualConfig

    def health(self) -> Health:
        return Health(
            status="ok",
            code=None,
            message="Source manuelle active.",
            checked_at=datetime.now(timezone.utc),
        )

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
        raise CapabilityNotSupported(self.type, "latest")

    def detect(
        self,
        url: str,
        *,
        quality: str | None = None,
        language: str | None = None,
        season: int | None = None,
        episode: int | None = None,
        filename: str | None = None,
    ) -> LinkCandidate:
        cleaned = url.strip()
        kind = classify_url(cleaned)
        hoster = hoster_of(cleaned) if kind == "ddl_hoster" else None
        return LinkCandidate(
            url=cleaned,
            kind=kind,
            hoster=hoster,
            quality=quality,
            language=language,
            filename=filename or filename_of(cleaned),
            season=season,
            episode=episode,
            external_id=external_id_for(cleaned),
        )


def classify_url(url: str) -> LinkKind:
    lowered = url.strip().lower()
    if lowered.startswith("magnet:"):
        return "magnet"
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()
    if path.endswith(".torrent") or lowered.startswith("torrent:"):
        return "torrent"
    if any(marker in host or marker in lowered for marker in _PROTECTORS):
        return "ddl_protected"
    if hoster_of(url):
        return "ddl_hoster"
    return "direct"


def hoster_of(url: str) -> str | None:
    host = (urlparse(url.strip()).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    for suffix, name in _HOSTERS:
        if host == suffix or host.endswith("." + suffix):
            return name
    return None


def filename_of(url: str) -> str | None:
    path = urlparse(url.strip()).path
    name = path.rsplit("/", 1)[-1]
    if not name or "." not in name:
        return None
    return name


def external_id_for(url: str) -> str:
    digest = hashlib.sha256(url.strip().encode("utf-8")).hexdigest()
    return f"manual:{digest[:32]}"
