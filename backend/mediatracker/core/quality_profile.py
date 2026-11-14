"""Named quality profiles shared by every download source.

A profile is a preference order: languages first, then resolution, then seeders
and size. Wawacity pages and Comet magnets both pass through the same checks.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

LANGUAGE_CODES = ("VFF", "VFQ", "VF", "MULTI", "VOSTFR", "VO", "EN")
LANGUAGE_ALIASES = {
    "TRUEFRENCH": "VFF",
    "FRENCH": "VF",
    "FR": "VF",
    "ENGLISH": "EN",
    "ENG": "EN",
}
# Low to high. ``HDLight`` is the legacy 720p/light tier; ``HDLight1080`` sits above HD 720p.
RESOLUTION_RANK = {"SD": 1, "HDLight": 2, "HD": 3, "HDLight1080": 4, "1080p": 5, "4K": 6}
HDLIGHT1080_RESOLUTION = "HDLight1080"
DEFAULT_NAME = "VF/Multi 1080p"

_TOKEN = r"(?:^|[^a-z0-9])({name})(?:[^a-z0-9]|$)"


@dataclass(frozen=True)
class Profile:
    name: str
    languages: tuple[str, ...]
    min_resolution: str | None
    max_resolution: str | None
    min_size_mb: int | None
    max_size_mb: int | None
    exclude_keywords: tuple[str, ...]
    quality_profile_id: int | None = None


@dataclass(frozen=True)
class ReleaseCandidate:
    """What every source can say about one file, before a profile accepts it."""

    language: str | None
    resolution: str | None
    size: int | None = None
    seeders: int | None = None
    text: str = ""


def default_profile() -> Profile:
    return Profile(
        name=DEFAULT_NAME,
        languages=("VFF", "VFQ", "VF", "MULTI", "VOSTFR"),
        min_resolution="1080p",
        max_resolution="1080p",
        min_size_mb=None,
        max_size_mb=None,
        exclude_keywords=(),
    )


def builtin_profiles() -> tuple[Profile, ...]:
    """Profiles every install gets. The first one stays the default."""
    return (
        default_profile(),
        Profile(
            name="4K Multi",
            languages=("MULTI", "VFF", "VF"),
            min_resolution="4K",
            max_resolution="4K",
            min_size_mb=None,
            max_size_mb=None,
            exclude_keywords=(),
        ),
        Profile(
            name="720p VF",
            languages=("VFF", "VFQ", "VF"),
            min_resolution="HD",
            max_resolution="HD",
            min_size_mb=None,
            max_size_mb=None,
            exclude_keywords=(),
        ),
        Profile(
            name="VOSTFR 1080p",
            languages=("VOSTFR",),
            min_resolution=None,
            max_resolution="1080p",
            min_size_mb=None,
            max_size_mb=None,
            exclude_keywords=(),
        ),
        Profile(
            name="Meilleure dispo",
            languages=("VFF", "VFQ", "VF", "MULTI", "VOSTFR", "VO"),
            min_resolution=None,
            max_resolution=None,
            min_size_mb=None,
            max_size_mb=None,
            exclude_keywords=(),
        ),
        Profile(
            name="HDLight VF/Multi",
            languages=("VFF", "VFQ", "VF", "MULTI"),
            min_resolution=HDLIGHT1080_RESOLUTION,
            max_resolution=HDLIGHT1080_RESOLUTION,
            min_size_mb=None,
            max_size_mb=None,
            exclude_keywords=(),
        ),
    )


def normalize_language(value: str | None) -> str | None:
    if not value:
        return None
    token = re.sub(r"[^A-Za-z]+", "", value).upper()
    if token in LANGUAGE_ALIASES:
        return LANGUAGE_ALIASES[token]
    if token in LANGUAGE_CODES:
        return token
    return detect_language(value)


def normalize_resolution(value: str | None) -> str | None:
    if not value:
        return None
    if value in RESOLUTION_RANK:
        return value
    folded = re.sub(r"[\s_\-]+", "", value).lower()
    if any(token in folded for token in ("2160", "uhd", "4k")):
        return "4K"
    if "hdlight" in folded and "1080" in folded:
        return HDLIGHT1080_RESOLUTION
    if "1080" in folded:
        return "1080p"
    if "hdlight" in folded:
        return "HDLight"
    if "480" in folded or folded == "sd":
        return "SD"
    if any(token in folded for token in ("720", "webrip", "webdl", "hdtv")) or folded == "hd":
        return "HD"
    return None


def detect_language(text: str) -> str | None:
    """Language of one release name or Comet description.

    Filename tokens win. A French flag counts as VF when the name has no token.
    VOSTFR stays VOSTFR even when the description also shows 🇫🇷.
    """
    if not text:
        return None
    lowered = text.lower()
    has_vostfr = _has(lowered, "vostfr")
    has_truefrench = _has(lowered, "truefrench")
    has_vff = _has(lowered, "vff")
    has_vfq = _has(lowered, "vfq")
    has_multi = _has(lowered, "multi")
    has_vf = _has(lowered, "vf")
    has_french = _has(lowered, "french") and not has_truefrench
    has_vo = _has(lowered, "vo") and not has_vostfr
    french_flag = "🇫🇷" in text
    english_flag = any(flag in text for flag in ("🇬🇧", "🇺🇸", "🇦🇺"))
    if has_multi:
        return "MULTI"
    if has_vostfr and not (has_truefrench or has_vff or has_vfq or has_vf):
        return "VOSTFR"
    if has_truefrench or has_vff:
        return "VFF"
    if has_vfq:
        return "VFQ"
    if has_vf or has_french or french_flag:
        return "VF"
    if has_vo:
        return "VO"
    if english_flag or _has(lowered, "english") or _has(lowered, "eng"):
        return "EN"
    return None


def resolution_allowed(resolution: str | None, profile: Profile) -> bool:
    if resolution is None:
        return profile.min_resolution is None and profile.max_resolution is None
    rank = RESOLUTION_RANK.get(resolution)
    if rank is None:
        return False
    if profile.min_resolution and rank < RESOLUTION_RANK[profile.min_resolution]:
        return False
    if profile.max_resolution and rank > RESOLUTION_RANK[profile.max_resolution]:
        return False
    return True


def profile_has_size_bounds(profile: Profile) -> bool:
    """True when the profile defines at least one size bound (shared with ``size_allowed``)."""
    return profile.min_size_mb is not None or profile.max_size_mb is not None


def size_allowed(size: int | None, profile: Profile) -> bool:
    if not profile_has_size_bounds(profile):
        return True
    if size is None or size <= 0:
        return False
    megabytes = size / (1024 * 1024)
    if profile.min_size_mb is not None and megabytes < profile.min_size_mb:
        return False
    return profile.max_size_mb is None or megabytes <= profile.max_size_mb


def keyword_excluded(text: str, profile: Profile) -> bool:
    folded = (text or "").casefold()
    return any(word.casefold() in folded for word in profile.exclude_keywords if word.strip())


def passes(candidate: ReleaseCandidate, profile: Profile, *, enforce_size: bool) -> bool:
    if keyword_excluded(candidate.text, profile):
        return False
    if candidate.language not in profile.languages:
        return False
    if not resolution_allowed(candidate.resolution, profile):
        return False
    return not enforce_size or size_allowed(candidate.size, profile)


def sort_key(candidate: ReleaseCandidate, profile: Profile) -> tuple[int, int, int, int]:
    """Lower is better: language order, then higher resolution, seeders, and size."""
    language_index = profile.languages.index(candidate.language or "")
    resolution = -RESOLUTION_RANK.get(candidate.resolution or "", 0)
    return (language_index, resolution, -(candidate.seeders or 0), -(candidate.size or 0))


def acceptance_rank(candidate: ReleaseCandidate, profile: Profile, *, enforce_size: bool = False) -> int | None:
    """Single penalty for a page or a hoster link. ``None`` means refused."""
    if not passes(candidate, profile, enforce_size=enforce_size):
        return None
    language_index, resolution, _seeders, _size = sort_key(candidate, profile)
    return language_index * 100 + resolution


def filter_candidates(
    candidates: list[ReleaseCandidate],
    profile: Profile,
    *,
    enforce_size: bool = True,
) -> tuple[list[int], int]:
    """Kept indexes, best first, and how many candidates the profile removed."""
    ranked: list[tuple[tuple[int, int, int, int], int]] = []
    dropped = 0
    for index, candidate in enumerate(candidates):
        if not passes(candidate, profile, enforce_size=enforce_size):
            dropped += 1
            continue
        ranked.append((sort_key(candidate, profile), index))
    ranked.sort(key=lambda item: item[0])
    return [index for _key, index in ranked], dropped


def profile_from_row(row: object) -> Profile:
    languages = tuple(str(item) for item in (getattr(row, "languages", None) or []))
    keywords = tuple(str(item) for item in (getattr(row, "exclude_keywords", None) or []))
    row_id = getattr(row, "id", None)
    return Profile(
        name=str(getattr(row, "name", "") or ""),
        languages=languages,
        min_resolution=getattr(row, "min_resolution", None),
        max_resolution=getattr(row, "max_resolution", None),
        min_size_mb=getattr(row, "min_size_mb", None),
        max_size_mb=getattr(row, "max_size_mb", None),
        exclude_keywords=keywords,
        quality_profile_id=int(row_id) if row_id is not None else None,
    )


def profile_for_media(db: Session, media: object) -> Profile | None:
    """The fiche override, or the global default. ``None`` only when nothing is seeded."""
    from mediatracker.db.models import QualityProfile

    row = None
    profile_id = getattr(media, "quality_profile_id", None)
    if profile_id:
        row = db.get(QualityProfile, int(profile_id))
    if row is None:
        row = db.scalar(select(QualityProfile).where(QualityProfile.is_default.is_(True)).limit(1))
    if row is None:
        return None
    return profile_from_row(row)


def preferences_for(profile: Profile):
    """Wawacity listing codes for this profile. The profile still filters the pages."""
    from mediatracker.providers.base import SourcePreferences

    languages = list(profile.languages) or ["VFF"]
    first = next((lang for lang in languages if lang in {"VFF", "VF", "VFQ"}), languages[0])
    if first == "MULTI":
        first = "VFF"
    max_resolution = profile.max_resolution or "1080p"
    if max_resolution == "4K":
        quality = "4K"
    elif max_resolution == "SD":
        quality = "SD"
    else:
        quality = "HD"
    allowed = [name for name in RESOLUTION_RANK if resolution_allowed(name, profile)]
    wider = any(lang in {"VOSTFR", "VO"} for lang in languages) or "4K" in allowed or "SD" in allowed
    return SourcePreferences(
        quality=quality,
        language=first,
        allow_fallback=wider,
        quality_fallback=allowed,
        language_fallback=languages,
    )


def comet_config_token(profile: Profile) -> str:
    """Comet's base64 config segment. Never carries a debrid key."""
    import base64

    french = any(lang in {"VFF", "VFQ", "VF", "MULTI", "VOSTFR"} for lang in profile.languages)
    english = any(lang in {"EN", "VO", "MULTI", "VOSTFR"} for lang in profile.languages)
    preferred: list[str] = []
    if french:
        preferred.append("fr")
    if "EN" in profile.languages or "VO" in profile.languages:
        preferred.append("en")
    payload = {
        "debridService": "torrent",
        "debridApiKey": "",
        "languages": {
            "required": [],
            "exclude": [] if english else ["en"],
            "preferred": preferred,
        },
        "resolutions": {
            "2160p": resolution_allowed("4K", profile),
            "1080p": resolution_allowed("1080p", profile),
            "720p": resolution_allowed("HD", profile) or resolution_allowed("HDLight", profile),
            "480p": resolution_allowed("SD", profile),
            "unknown": profile.max_resolution is None and profile.min_resolution is None,
        },
        "options": {
            "remove_ranks_under": -10_000_000_000,
            "allow_english_in_languages": english,
            "remove_unknown_languages": False,
        },
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def ensure_default_profile(db: Session) -> None:
    """Insert any built-in profile that is missing. An existing default stays default."""
    from mediatracker.db.models import QualityProfile

    rows = list(db.scalars(select(QualityProfile)).all())
    names = {row.name for row in rows}
    has_default = any(row.is_default for row in rows)
    added = False
    for seed in builtin_profiles():
        if seed.name in names:
            continue
        make_default = seed.name == DEFAULT_NAME and not has_default
        db.add(
            QualityProfile(
                name=seed.name,
                languages=list(seed.languages),
                min_resolution=seed.min_resolution,
                max_resolution=seed.max_resolution,
                min_size_mb=seed.min_size_mb,
                max_size_mb=seed.max_size_mb,
                exclude_keywords=list(seed.exclude_keywords),
                is_default=make_default,
            )
        )
        if make_default:
            has_default = True
        added = True
    if added:
        db.commit()


def _has(lowered: str, name: str) -> bool:
    return re.search(_TOKEN.format(name=re.escape(name)), lowered) is not None
