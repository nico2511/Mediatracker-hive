"""Pick a source page by title, year, season, quality, and language.

This stays on the shared result shape. It does not know how a site is scraped.
"""

import re
import unicodedata
from collections.abc import Sequence

from mediatracker.core.quality_profile import (
    Profile,
    acceptance_rank,
    release_candidate_from_fields,
)
from mediatracker.core.release_title import split_release_title
from mediatracker.providers.base import SourceResult


def fold_title(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value or "")
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    folded = folded.lower()
    return re.sub(r"[^a-z0-9]+", " ", folded).strip()


def release_core(value: str) -> str:
    """Title without trailing version tags, repeated dashes, or season markers.

    « Détective Démo - - VF HD » and « Détective Démo - 4K UHD » both reduce to Détective Démo.
    A token is only dropped from the end, so « HD Forever » stays intact.
    """
    title, _tokens = split_release_title(value or "")
    title = re.sub(r"(?i)\b(?:saison|season)\s*\d+\b", " ", title)
    title = re.sub(r"[-–—|:/]+", " ", title)
    return fold_title(title)


def titles_match(left: str, right: str) -> bool:
    folded = release_core(left)
    return folded == release_core(right) and folded != ""


def identity_match(
    candidate_title: str,
    candidate_year: int | None,
    *,
    title: str,
    original_title: str | None,
    year: int | None,
    media_type: str,
) -> bool:
    """Exact local or original title. A film also needs the same year.

    Substrings do not count: Pitch Black is not Les Ensorceleuses 2, and a
    page that forgot its title is not the search phrase.
    """
    names = [name for name in (title, original_title or "") if name.strip()]
    if not (candidate_title or "").strip() or not any(titles_match(candidate_title, name) for name in names):
        return False
    if year is None:
        return True
    if media_type == "film":
        return candidate_year == year
    return candidate_year is None or candidate_year == year


def canonical_quality(value: str | None) -> str | None:
    """Map release tokens (WEBRIP, WEB-DL 1080p) onto the qualities the fiche stores."""
    if not value:
        return None
    folded = re.sub(r"[\s_\-]+", "", value).lower()
    if any(token in folded for token in ("2160", "uhd", "4k")):
        return "4K"
    if "hdlight" in folded and "1080" in folded:
        return "HDLight1080"
    if "1080" in folded:
        return "1080p"
    if "hdlight" in folded:
        return "HDLight"
    if "480" in folded or folded == "sd":
        return "SD"
    if any(token in folded for token in ("720", "webrip", "webrrip", "webdl", "hdtv")) or folded == "hd":
        return "HD"
    return value


def canonical_language(value: str | None) -> str | None:
    """TRUEFRENCH is VFF. A combined « MULTI (TRUEFRENCH) » stays VFF."""
    if not value:
        return None
    folded = re.sub(r"[\s_\-]+", "", value).lower()
    if "vostfr" in folded:
        return "VOSTFR"
    if "truefrench" in folded or "vff" in folded or "vf2" in folded:
        return "VFF"
    if "vfq" in folded:
        return "VFQ"
    from mediatracker.core.quality_profile import _has_contextual_ca

    if _has_contextual_ca(value):
        return "VFQ"
    if "multi" in folded:
        return "MULTI"
    if re.search(r"(^|[^a-z])vf([^a-z]|$)", value.lower()):
        return "VF"
    if folded == "french" or re.search(r"(^|[^a-z])french([^a-z]|$)", value.lower()):
        return "VF"
    if folded == "vo":
        return "VO"
    return value


def variant_rank(
    result: SourceResult,
    *,
    quality: str,
    language: str,
    allow_fallback: bool,
    quality_fallback: Sequence[str],
    language_fallback: Sequence[str],
) -> int | None:
    """Lower is better. ``None`` means the page is refused."""
    if result.rejection:
        return None
    page_quality = canonical_quality(result.quality)
    page_language = canonical_language(result.language)
    if page_quality == quality and page_language == language:
        return 0
    if not allow_fallback or page_quality is None or page_language is None:
        return None
    languages = list(language_fallback)
    qualities = list(quality_fallback)
    if page_quality == quality and page_language in languages:
        return 1 + languages.index(page_language)
    if page_language == language and page_quality in qualities:
        return 20 + qualities.index(page_quality)
    if page_quality in qualities and page_language in languages:
        return 40 + qualities.index(page_quality) + languages.index(page_language)
    return None


def select_variants(
    results: list[SourceResult],
    *,
    title: str,
    year: int | None,
    media_type: str,
    original_title: str | None = None,
    seasons: set[int],
    quality: str,
    language: str,
    allow_fallback: bool,
    quality_fallback: Sequence[str],
    language_fallback: Sequence[str],
    exclude: set[tuple[str | None, str | None]] | None = None,
    profile: Profile | None = None,
) -> list[SourceResult]:
    """One page per season (or one film page). Preferred quality and language win."""
    ranked: list[tuple[int, SourceResult]] = []
    for result in results:
        if result.media_type and result.media_type != media_type:
            continue
        if exclude and (result.quality, result.language) in exclude:
            continue
        if not identity_match(
            result.title,
            result.year,
            title=title,
            original_title=original_title,
            year=year,
            media_type=media_type,
        ):
            continue
        if media_type == "series" and result.season is not None and result.season not in seasons:
            continue
        rank = _page_rank(
            result,
            quality=quality,
            language=language,
            allow_fallback=allow_fallback,
            quality_fallback=quality_fallback,
            language_fallback=language_fallback,
            profile=profile,
        )
        if rank is None:
            continue
        ranked.append((rank, result))
    if media_type != "film" and year is not None and any(item.year == year for _, item in ranked):
        ranked = [(rank, item) for rank, item in ranked if item.year == year]
    best: dict[int | None, tuple[int, SourceResult]] = {}
    for rank, result in ranked:
        key = result.season if media_type == "series" else None
        current = best.get(key)
        if current is None or rank < current[0]:
            best[key] = (rank, result)
    chosen = [item for _, item in best.values()]
    chosen.sort(key=lambda item: (item.season or 0, item.ref))
    return chosen


def explain_misses(
    results: list[SourceResult],
    *,
    title: str,
    year: int | None,
    media_type: str,
    original_title: str | None = None,
    seasons: set[int],
    quality: str,
    language: str,
    allow_fallback: bool,
    quality_fallback: Sequence[str],
    language_fallback: Sequence[str],
    profile: Profile | None = None,
) -> str:
    """One French sentence per distinct reason a candidate was left aside. No URLs."""
    notes: list[str] = []
    for result in results:
        label = result.title or "Sans titre"
        if result.rejection:
            notes.append(result.rejection if result.rejection.startswith("«") else f"« {label} » : {result.rejection}")
            continue
        if result.media_type and result.media_type != media_type:
            notes.append(f"« {label} » n'est pas un {('film' if media_type == 'film' else 'série')}.")
            continue
        if not identity_match(
            result.title,
            result.year,
            title=title,
            original_title=original_title,
            year=year,
            media_type=media_type,
        ):
            if (
                year is not None
                and result.year not in {None, year}
                and any(titles_match(result.title, name) for name in (title, original_title or "") if name.strip())
            ):
                notes.append(f"« {label} » est de {result.year}, pas {year}.")
            elif (
                media_type == "film"
                and year is not None
                and result.year is None
                and any(titles_match(result.title, name) for name in (title, original_title or "") if name.strip())
            ):
                notes.append(f"« {label} » n'a pas l'année {year}.")
            else:
                notes.append(f"« {label} » ne correspond pas au titre.")
            continue
        if media_type == "series" and result.season is not None and result.season not in seasons:
            shown = result.season if result.season is not None else "?"
            notes.append(f"Saison {shown} de « {label} » hors des épisodes attendus.")
            continue
        rank = _page_rank(
            result,
            quality=quality,
            language=language,
            allow_fallback=allow_fallback,
            quality_fallback=quality_fallback,
            language_fallback=language_fallback,
            profile=profile,
        )
        if rank is None:
            if profile is not None:
                notes.append(
                    f"« {label} » est en {result.quality or '?'} {result.language or '?'}, "
                    f"hors du profil « {profile.name} »."
                )
            else:
                notes.append(
                    f"« {label} » est en {result.quality or '?'} {result.language or '?'}, pas {quality} {language}."
                )
    unique = list(dict.fromkeys(notes))
    return " ".join(unique[:3])


_EMPTY_SEARCH_MESSAGE = "Aucun résultat sur les sources de recherche pour ce titre."


def no_version_message(
    results: Sequence[SourceResult],
    profile_name: str,
    detail: str = "",
) -> str:
    """User-facing poll message when no catalog page is kept for the attempt."""
    stripped = (detail or "").strip()
    if not results:
        if stripped:
            return stripped
        return _EMPTY_SEARCH_MESSAGE
    message = f"Aucune version ne correspond au profil « {profile_name} »."
    if stripped:
        message = f"{message} {stripped}"
    return message


def _page_rank(
    result: SourceResult,
    *,
    quality: str,
    language: str,
    allow_fallback: bool,
    quality_fallback: Sequence[str],
    language_fallback: Sequence[str],
    profile: Profile | None,
) -> int | None:
    if profile is None:
        return variant_rank(
            result,
            quality=quality,
            language=language,
            allow_fallback=allow_fallback,
            quality_fallback=quality_fallback,
            language_fallback=language_fallback,
        )
    if result.rejection:
        return None
    return acceptance_rank(
        release_candidate_from_fields(
            language=result.language,
            quality=result.quality,
            hint=result.profile_hint,
            title=result.title,
        ),
        profile,
    )
