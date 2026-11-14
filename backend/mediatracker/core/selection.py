"""Default quality, language, and hoster order. The download pipeline (M2) consumes this."""

from collections.abc import Sequence

DEFAULT_QUALITY = "HD"
DEFAULT_LANGUAGE = "VFF"
# Historical pair. The allow-list is the registry (links or episode). This
# tuple is only the fallback when that registry has no download source yet.
DOWNLOAD_SOURCES = ("wawacity", "comet")
LEGACY_DOWNLOAD_SOURCE_ORDER = "wawacity,comet"
DEFAULT_DOWNLOAD_SOURCE_ORDER = "wawacity,free_telecharger,hive,comet,torrentio"
DEFAULT_HOSTER_PREFERENCE: tuple[str, ...] = (
    "rapidgator",
    "turbobit",
    "uploady",
    "dailyuploads",
    "1fichier",
)
# Still recognised if the person adds them to the order in Réglages.
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
# Closed or forbidden hosters (single block list for ingestion and settings).
BLOCKED_SOURCE_HOSTERS = frozenset({"uptobox"})
EXCLUDED_HOSTERS = BLOCKED_SOURCE_HOSTERS
HOSTER_LAST = "1fichier"

QUALITY_OPTIONS = ("HD", "1080p", "4K", "HDLight", "SD")
LANGUAGE_OPTIONS = ("VFF", "VFQ", "VF", "MULTI", "VOSTFR", "VO")
QUALITIES = frozenset(QUALITY_OPTIONS)
LANGUAGES = frozenset(LANGUAGE_OPTIONS)
# Fallback order, applied only when allow_fallback is on (global or per media).
LANGUAGE_FALLBACK = ("VFF", "MULTI", "VF")
QUALITY_FALLBACK = ("HD", "1080p", "HDLight")


def registered_download_sources() -> tuple[str, ...]:
    """Download source types, in registry rank order. Not alphabetical."""
    from mediatracker.providers.sources.loading import download_source_types

    found = download_source_types()
    return found or DOWNLOAD_SOURCES


def parse_download_source_order(raw: str, allowed: Sequence[str] | None = None) -> list[str]:
    """Comma-separated source types. A name outside the registry is refused.

    ``allowed`` overrides the registry. Empty pieces are ignored. The default
    string stays ``DEFAULT_DOWNLOAD_SOURCE_ORDER``; the registry does not reorder a saved list.
    """
    known = tuple(allowed) if allowed is not None else registered_download_sources()
    items: list[str] = []
    for part in raw.split(","):
        name = part.strip().lower()
        if not name:
            continue
        if name not in known:
            raise ValueError(f"Source inconnue : {name}.")
        if name not in items:
            items.append(name)
    if not items:
        raise ValueError("L'ordre des sources est vide.")
    return items


def ensure_1fichier_last(items: list[str]) -> list[str]:
    """1fichier is always last in the effective hoster order."""
    head = [name for name in items if name != HOSTER_LAST]
    return head + [HOSTER_LAST]


def parse_hoster_preference(raw: str) -> list[str]:
    items = []
    for part in raw.split(","):
        name = part.strip().lower()
        if not name:
            continue
        if name in EXCLUDED_HOSTERS or name in BLOCKED_SOURCE_HOSTERS:
            raise ValueError(f"L'hébergeur « {name} » est exclu.")
        if name not in KNOWN_HOSTERS:
            raise ValueError(f"Hébergeur inconnu : {name}.")
        if name not in items:
            items.append(name)
    if not items:
        raise ValueError("La préférence d'hébergeurs est vide.")
    return ensure_1fichier_last(items)


def canonical_choice(token: str, options: Sequence[str]) -> str | None:
    folded = token.strip().lower()
    for item in options:
        if item.lower() == folded:
            return item
    return None


def parse_choice_list(raw: str, options: Sequence[str]) -> list[str]:
    found: list[str] = []
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        canon = canonical_choice(token, options)
        if canon is None:
            raise ValueError(f"Valeur inconnue : {token}.")
        if canon not in found:
            found.append(canon)
    return found


def hoster_allowed(hoster: str | None, preference: Sequence[str]) -> bool:
    if not hoster:
        return False
    name = hoster.lower()
    if name in BLOCKED_SOURCE_HOSTERS:
        return False
    return name in {item.lower() for item in preference}


def hoster_rank(hoster: str | None, preference: Sequence[str]) -> int:
    if not hoster:
        return len(preference) + 1
    lowered = hoster.lower()
    if lowered in EXCLUDED_HOSTERS:
        return len(preference) + 2
    try:
        return list(preference).index(lowered)
    except ValueError:
        return len(preference)
