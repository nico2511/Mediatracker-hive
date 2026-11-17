"""Single registry for hoster names, blocked list, and domain / page aliases."""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import urlparse

KNOWN_HOSTERS = frozenset(
    {
        "rapidgator",
        "turbobit",
        "uploady",
        "dailyuploads",
        "nitroflare",
        "katfile",
        "tezfiles",
        "1fichier",
    }
)
BLOCKED_SOURCE_HOSTERS = frozenset({"uptobox"})
EXCLUDED_HOSTERS = BLOCKED_SOURCE_HOSTERS
HOSTER_LAST = "1fichier"

# Sort order when scraping Wawacity / Free-Telecharger (matches historical _KEEP_HOSTERS).
KNOWN_HOSTER_RANK_ORDER: tuple[str, ...] = (
    "rapidgator",
    "turbobit",
    "uploady",
    "dailyuploads",
    "nitroflare",
    "katfile",
    "tezfiles",
    "1fichier",
)

# Hostname suffix → canonical hoster (manual URL detection and related).
URL_DOMAIN_TO_HOSTER: tuple[tuple[str, str], ...] = (
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

# Substrings in scraped page text → canonical hoster (longer aliases first).
PAGE_TEXT_TO_HOSTER: tuple[tuple[str, str], ...] = (
    ("dailyuploads", "dailyuploads"),
    ("daily-uploads", "dailyuploads"),
    ("daily uploads", "dailyuploads"),
    ("nitroflare", "nitroflare"),
    ("rapidgator", "rapidgator"),
    ("turbobit", "turbobit"),
    ("trbt.cc", "turbobit"),
    ("rg.to", "rapidgator"),
    ("1fichier", "1fichier"),
    ("uptobox", "uptobox"),
    ("uploady", "uploady"),
    ("tezfiles", "tezfiles"),
    ("katfile", "katfile"),
)

# Hostname fragments stripped from payloads before they leave the process (see events.scrub).
SCRUB_URL_HOSTER_MARKERS: tuple[str, ...] = (
    "alldebrid",
    "rapidgator",
    "turbobit",
    "nitroflare",
    "katfile",
    "tezfiles",
    "1fichier",
    *BLOCKED_SOURCE_HOSTERS,
)


def hoster_is_blocked(name: str | None) -> bool:
    return bool(name) and name.casefold() in BLOCKED_SOURCE_HOSTERS


def hoster_from_url(url: str) -> str | None:
    host = (urlparse(url.strip()).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    for suffix, canonical in URL_DOMAIN_TO_HOSTER:
        if host == suffix or host.endswith("." + suffix):
            return canonical
    return None


def source_ingestion_hosters(hoster_preference: Sequence[str] | None = None) -> tuple[str, ...]:
    """Known hosters ∪ configured preference, minus blocked (for source link filtering)."""
    blocked = BLOCKED_SOURCE_HOSTERS
    allowed: set[str] = {name for name in KNOWN_HOSTERS if name not in blocked}
    if hoster_preference:
        for raw in hoster_preference:
            name = raw.strip().lower()
            if name and name not in blocked:
                allowed.add(name)
    ordered: list[str] = []
    if hoster_preference:
        for raw in hoster_preference:
            name = raw.strip().lower()
            if name in allowed and name not in ordered:
                ordered.append(name)
    for name in KNOWN_HOSTER_RANK_ORDER:
        if name in allowed and name not in ordered:
            ordered.append(name)
    for name in sorted(allowed - set(ordered), key=str.casefold):
        ordered.append(name)
    return tuple(ordered)


def blocked_hoster_names() -> list[str]:
    return sorted(BLOCKED_SOURCE_HOSTERS, key=str.casefold)
