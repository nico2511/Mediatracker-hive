"""Wawacity listing codes and search URLs.

Nothing else in the app should know these ``s=`` values. The core asks for a
media type, a language and a quality; this module turns that into one or more
Wawacity listing filters.

Series listings (language and quality are the page itself):

- ``vf`` — VF SD
- ``vf-hq`` — VF HD, the default
- ``vostfr`` / ``vostfr-hq`` — only when VOSTFR is enabled
- ``multi-4k`` — MULTI 4K, only when 4K is enabled

Film listings (quality or category; the language is on each release label):

- ``blu-ray_1080p-720p`` — HD, the default
- ``ultra-hd-4k`` — 4K, only when 4K is enabled
- ``dessins_animes`` — animation, kept when the label is an accepted HD release
- ``dvdrip-hq`` / ``dvdrip-dbrip`` — SD, only as a quality fallback
- ``dvdsrc-r5-ts-cam`` — cam / TS, never requested
- ``film-vostfr`` — only when VOSTFR is enabled
- ``_film-vo`` — original language, only when VO is enabled
- ``vieux-films`` — older films, filtered by the label
"""

from urllib.parse import quote_plus, urlsplit, urlunsplit

from mediatracker.providers.base import SourcePreferences

_HD = frozenset({"HD", "1080p", "720p", "HDLight"})
_FRENCH = frozenset({"VFF", "VF", "VFQ", "MULTI"})
_CAM_SLUG = "dvdsrc-r5-ts-cam"


def search_slugs(media_type: str, preferences: SourcePreferences) -> list[str]:
    """Preferred listing first, then the fallbacks the user actually allowed."""
    quality = preferences.quality or "HD"
    language = preferences.language or "VFF"
    fallback = preferences.allow_fallback
    qualities = set(preferences.quality_fallback)
    languages = set(preferences.language_fallback)
    want_4k = quality == "4K" or (fallback and "4K" in qualities)
    want_sd = quality == "SD" or (fallback and "SD" in qualities)
    want_vostfr = language == "VOSTFR" or (fallback and "VOSTFR" in languages)
    want_vo = language == "VO" or (fallback and "VO" in languages)
    if media_type == "series":
        return _series_slugs(quality, language, want_4k, want_sd, want_vostfr, fallback)
    return _film_slugs(quality, language, want_4k, want_sd, want_vostfr, want_vo, fallback)


def listing_slug(media_type: str, quality: str, language: str) -> str:
    """One page for Nouveautés. No fallbacks, so a refresh stays a single request."""
    slugs = search_slugs(
        media_type,
        SourcePreferences(quality=quality or "HD", language=language or "VFF", allow_fallback=False),
    )
    return slugs[0]


def search_url(base_url: str, media_type: str, query: str, slug: str) -> str:
    category = "films" if media_type == "film" else "series"
    text = quote_plus(query.strip())
    return f"{base_url.rstrip('/')}/?p={category}&search={text}&s={slug}"


def plain_search_url(base_url: str, media_type: str, query: str, *, year: int | None) -> str:
    """Film (and optional) search without a listing ``s=`` filter; year goes in the query string."""
    category = "films" if media_type == "film" else "series"
    text = quote_plus(query.strip())
    url = f"{base_url.rstrip('/')}/?p={category}&search={text}"
    if year is not None:
        url += f"&year={year}"
    return url


def film_discover_urls(title: str, original_title: str | None, year: int | None) -> list[tuple[str, int | None]]:
    """French title first (with year, then without). Original title is not included here."""
    cleaned = " ".join(title.split())
    if not cleaned:
        return []
    urls: list[tuple[str, int | None]] = []
    if year is not None:
        urls.append((cleaned, year))
    urls.append((cleaned, None))
    return urls


def original_discover_urls(original_title: str | None, year: int | None) -> list[tuple[str, int | None]]:
    cleaned = " ".join((original_title or "").split())
    if not cleaned:
        return []
    urls: list[tuple[str, int | None]] = []
    if year is not None:
        urls.append((cleaned, year))
    urls.append((cleaned, None))
    return urls


def listing_url(base_url: str, media_type: str, slug: str) -> str:
    category = "films" if media_type == "film" else "series"
    return f"{base_url.rstrip('/')}/?p={category}&s={slug}"


def search_phrases(title: str, original_title: str | None, year: int | None) -> list[str]:
    """French title, then the original, each with and without the year."""
    phrases: list[str] = []
    for name in (title, original_title or ""):
        cleaned = " ".join(name.split())
        if not cleaned or cleaned in phrases:
            continue
        phrases.append(cleaned)
        if year is not None:
            with_year = f"{cleaned} {year}"
            if with_year not in phrases:
                phrases.append(with_year)
    return phrases


def apply_listing(
    quality: str | None,
    language: str | None,
    slug: str,
    *,
    media_type: str,
) -> tuple[str | None, str | None]:
    """Fill gaps from the listing, without inventing a language the label did not state.

    Films are split by quality, so a blu-ray page does not force VF. A series
    ``vf-hq`` page that only says « VF HD » is the French HD release (VFF).
    """
    if media_type == "series":
        if slug == "vf":
            quality = quality or "SD"
            language = language or "VF"
        elif slug == "vf-hq":
            quality = quality or "HD"
            if language in {None, "VF"} and quality in _HD:
                language = "VFF"
        elif slug == "vostfr":
            language = "VOSTFR"
            quality = quality or "SD"
        elif slug == "vostfr-hq":
            language = "VOSTFR"
            quality = quality or "HD"
        elif slug == "multi-4k":
            quality = "4K"
            language = language or "MULTI"
    else:
        if slug == "ultra-hd-4k":
            quality = quality or "4K"
        elif slug in {"dvdrip-hq", "dvdrip-dbrip"}:
            quality = quality or "SD"
        elif slug == "film-vostfr":
            language = language or "VOSTFR"
        elif slug == "_film-vo":
            language = language or "VO"
        elif slug == "blu-ray_1080p-720p" and quality is None:
            quality = "HD"
    if language == "VF" and quality in _HD:
        language = "VFF"
    return quality, language


def blocked_slug(slug: str) -> bool:
    return slug == _CAM_SLUG


def _series_slugs(
    quality: str,
    language: str,
    want_4k: bool,
    want_sd: bool,
    want_vostfr: bool,
    fallback: bool,
) -> list[str]:
    slugs: list[str] = []
    if language == "VOSTFR":
        slugs.append("vostfr" if quality == "SD" else "vostfr-hq")
    elif quality == "4K":
        slugs.append("multi-4k")
    elif quality == "SD":
        slugs.append("vf")
    else:
        slugs.append("vf-hq")
    if not fallback:
        return slugs
    if want_sd:
        _add(slugs, "vf")
    if want_4k:
        _add(slugs, "multi-4k")
    if want_vostfr:
        _add(slugs, "vostfr-hq")
        _add(slugs, "vostfr")
    if language in _FRENCH and "vf-hq" not in slugs and quality != "SD":
        _add(slugs, "vf-hq")
    return slugs


def _film_slugs(
    quality: str,
    language: str,
    want_4k: bool,
    want_sd: bool,
    want_vostfr: bool,
    want_vo: bool,
    fallback: bool,
) -> list[str]:
    slugs: list[str] = []
    if language == "VOSTFR":
        slugs.append("film-vostfr")
    elif language == "VO":
        slugs.append("_film-vo")
    elif quality == "4K":
        slugs.append("ultra-hd-4k")
    elif quality == "SD":
        slugs.extend(["dvdrip-hq", "dvdrip-dbrip"])
    else:
        slugs.extend(["blu-ray_1080p-720p", "dessins_animes", "vieux-films"])
    if fallback:
        if want_4k:
            _add(slugs, "ultra-hd-4k")
        if want_sd:
            _add(slugs, "dvdrip-hq")
            _add(slugs, "dvdrip-dbrip")
        if want_vostfr:
            _add(slugs, "film-vostfr")
        if want_vo:
            _add(slugs, "_film-vo")
        if language in _FRENCH and quality in _HD:
            _add(slugs, "blu-ray_1080p-720p")
    return [slug for slug in slugs if not blocked_slug(slug)]


def _add(slugs: list[str], slug: str) -> None:
    if slug not in slugs and not blocked_slug(slug):
        slugs.append(slug)


def slug_of(url: str) -> str | None:
    query = urlsplit(url).query
    for part in query.split("&"):
        if part.startswith("s="):
            return part[2:] or None
    return None


def without_slug(url: str) -> str:
    parts = urlsplit(url)
    kept = [item for item in parts.query.split("&") if item and not item.startswith("s=")]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "&".join(kept), parts.fragment))
