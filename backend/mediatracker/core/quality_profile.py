"""Named quality profiles shared by every download source.

A profile is a preference order: languages first, then resolution, then seeders
and size. Wawacity pages and Comet magnets both pass through the same checks.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

LANGUAGE_CODES = ("VFF", "VFQ", "VF", "MULTI", "VOSTFR", "VO", "EN")
LANGUAGE_ALIASES = {
    "TRUEFRENCH": "VFF",
    "VF2": "VFF",
    "FRENCH": "VF",
    "FR": "VF",
    "ENGLISH": "EN",
    "ENG": "EN",
}
# Low to high. ``HDLight`` is the legacy 720p/light tier; ``HDLight1080`` sits above HD 720p.
RESOLUTION_RANK = {"SD": 1, "HDLight": 2, "HD": 3, "HDLight1080": 4, "1080p": 5, "4K": 6}
HDLIGHT1080_RESOLUTION = "HDLight1080"
DEFAULT_NAME = "VF/Multi 1080p"
PROVISIONAL_HD_SORT_PENALTY = 20
_EXPLICIT_RESOLUTION = re.compile(r"(?i)\b(?:2160p|1080p|720p|480p|4k|uhd)\b")

_TOKEN = r"(?:^|[^a-z0-9])({name})(?:[^a-z0-9]|$)"
_LANG_BEFORE_CA = re.compile(r"(?i)(?:multi|vff|vfq|vf2|truefrench|french|vostfr|vf)[.\s_\-()]*ca(?:[^a-z0-9]|$)")
_CA_BEFORE_LANG = re.compile(
    r"(?i)(?:^|[^a-z0-9])ca[.\s_\-()]*(?:multi|vff|vfq|vf2|truefrench|french|vostfr|vf)(?:[^a-z0-9]|$)"
)


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
    token = re.sub(r"[^A-Za-z0-9]+", "", value).upper()
    if token in LANGUAGE_ALIASES:
        return LANGUAGE_ALIASES[token]
    if token in LANGUAGE_CODES:
        return token
    return detect_language(value)


def explicit_resolution_in_text(text: str | None) -> bool:
    return bool(_EXPLICIT_RESOLUTION.search(text or ""))


def release_candidate_from_inspection(
    *,
    language: str | None,
    quality: str | None,
    filename: str,
    size: int | None = None,
) -> ReleaseCandidate:
    return ReleaseCandidate(
        language=normalize_language(language) or detect_language(filename),
        resolution=normalize_resolution(quality),
        size=size,
        text=filename or profile_rank_text(hint="", quality=quality, language=language),
    )


def wawacity_page_hd_label_candidate(candidate: object) -> bool:
    from mediatracker.providers.base import LinkCandidate

    if isinstance(candidate, LinkCandidate) and candidate.page_hd_label:
        return True
    filename = (getattr(candidate, "filename", None) or "").strip()
    if filename:
        return False
    quality = normalize_resolution(getattr(candidate, "quality", None))
    if quality != "HD":
        return False
    return not explicit_resolution_in_text(str(getattr(candidate, "quality", "") or ""))


def wawacity_page_hd_label_link(link: object) -> bool:
    filename = (getattr(link, "filename", None) or "").strip()
    if filename:
        return False
    if (getattr(link, "quality", None) or "").upper() != "HD":
        return False
    provider = getattr(link, "provider", None)
    if provider is not None and getattr(provider, "type", None) == "wawacity":
        return True
    from sqlalchemy import inspect as sa_inspect

    from mediatracker.db.models import Provider

    state = sa_inspect(link, raiseerr=False)
    session = state.session if state is not None else None
    provider_id = getattr(link, "provider_id", None)
    if session is not None and provider_id is not None:
        row = session.get(Provider, provider_id)
        if row is not None and row.type == "wawacity":
            return True
    return False


def provisional_hd_acceptance_rank(candidate: ReleaseCandidate, profile: Profile) -> int | None:
    if profile.max_resolution and RESOLUTION_RANK.get(profile.max_resolution, 0) < RESOLUTION_RANK["1080p"]:
        return None
    if profile.min_resolution and RESOLUTION_RANK.get(profile.min_resolution, 0) > RESOLUTION_RANK["1080p"]:
        return None
    language = candidate.language or detect_language(candidate.text)
    if not language or language not in profile.languages:
        return None
    probe = ReleaseCandidate(
        language=language,
        resolution="1080p",
        size=candidate.size,
        text=candidate.text,
    )
    base = acceptance_rank(probe, profile, enforce_size=False)
    if base is None:
        return None
    return base + PROVISIONAL_HD_SORT_PENALTY


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
    has_vf2 = _has(lowered, "vf2")
    has_vfq = _has(lowered, "vfq") or _has_contextual_ca(text)
    has_multi = _has(lowered, "multi")
    has_vf = _has(lowered, "vf") and not has_vf2
    has_french = _has(lowered, "french") and not has_truefrench
    has_vo = _has(lowered, "vo") and not has_vostfr
    french_flag = "🇫🇷" in text
    english_flag = any(flag in text for flag in ("🇬🇧", "🇺🇸", "🇦🇺"))
    if has_multi:
        return "MULTI"
    if has_vostfr and not (has_truefrench or has_vff or has_vfq or has_vf):
        return "VOSTFR"
    if has_truefrench or has_vff or has_vf2:
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


def multi_french_variant(text: str) -> str | None:
    """French audio track inside a MULTI release name (VFF before VFQ before VF)."""
    if not text:
        return None
    lowered = text.lower()
    if _has(lowered, "truefrench") or _has(lowered, "vff") or _has(lowered, "vf2"):
        return "VFF"
    if _has(lowered, "vfq") or _has_contextual_ca(text):
        return "VFQ"
    has_vf = _has(lowered, "vf") and not _has(lowered, "vf2")
    has_french = _has(lowered, "french") and not _has(lowered, "truefrench")
    if has_vf or has_french or "🇫🇷" in text:
        return "VF"
    return None


def _multi_french_rank(candidate: ReleaseCandidate, profile: Profile) -> int:
    if candidate.language != "MULTI":
        return 0
    variant = multi_french_variant(candidate.text)
    if variant is None:
        return len(profile.languages)
    try:
        return profile.languages.index(variant)
    except ValueError:
        return len(profile.languages)


def sort_key(candidate: ReleaseCandidate, profile: Profile) -> tuple[int, int, int, int, int]:
    """Lower is better: language, MULTI French track, resolution, seeders, size."""
    language_index = profile.languages.index(candidate.language or "")
    french_rank = _multi_french_rank(candidate, profile)
    resolution = -RESOLUTION_RANK.get(candidate.resolution or "", 0)
    return (language_index, french_rank, resolution, -(candidate.seeders or 0), -(candidate.size or 0))


def profile_rank_text(
    *,
    hint: str = "",
    title: str | None = None,
    quality: str | None = None,
    language: str | None = None,
) -> str:
    """Text fed to ``acceptance_rank`` so MULTi variants (VFF/VF2/VFQ/CA) stay visible."""
    stripped = (hint or "").strip()
    if stripped:
        return stripped
    return " ".join(part for part in (title, quality, language) if part and str(part).strip())


def release_candidate_from_fields(
    *,
    language: str | None,
    quality: str | None,
    hint: str = "",
    title: str | None = None,
) -> ReleaseCandidate:
    return ReleaseCandidate(
        language=normalize_language(language),
        resolution=normalize_resolution(quality),
        text=profile_rank_text(hint=hint, title=title, quality=quality, language=language),
    )


def acceptance_rank(candidate: ReleaseCandidate, profile: Profile, *, enforce_size: bool = False) -> int | None:
    """Single penalty for a page or a hoster link. ``None`` means refused."""
    if not passes(candidate, profile, enforce_size=enforce_size):
        return None
    language_index, french_rank, resolution, _seeders, _size = sort_key(candidate, profile)
    return language_index * 100 + french_rank * 10 + resolution


def filter_candidates(
    candidates: list[ReleaseCandidate],
    profile: Profile,
    *,
    enforce_size: bool = True,
) -> tuple[list[int], int]:
    """Kept indexes, best first, and how many candidates the profile removed."""
    ranked: list[tuple[tuple[int, int, int, int, int], int]] = []
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


def refresh_media_poll_schedule(media: object, now: datetime) -> None:
    """Force le prochain sondage sans attendre le cooldown recherche distante."""
    from datetime import timezone

    moment = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    media.last_remote_search_at = None  # type: ignore[attr-defined]
    media.next_source_poll_at = moment  # type: ignore[attr-defined]


def refresh_followed_media_for_profile_change(
    db: Session,
    profile_id: int,
    *,
    affects_default: bool,
    now: datetime,
) -> None:
    from datetime import timezone

    from sqlalchemy import or_, select

    from mediatracker.db.models import Media

    moment = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    clause = Media.quality_profile_id == profile_id
    if affects_default:
        clause = or_(clause, Media.quality_profile_id.is_(None))
    for media in db.scalars(select(Media).where(Media.followed.is_(True), clause)).all():
        media.last_remote_search_at = None
        media.next_source_poll_at = moment


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


def _has_contextual_ca(text: str) -> bool:
    """Canadian-french CA only beside MULTI or another language tag (not a lone title/group token)."""
    if not text:
        return False
    return _LANG_BEFORE_CA.search(text) is not None or _CA_BEFORE_LANG.search(text) is not None


def _has(lowered: str, name: str) -> bool:
    return re.search(_TOKEN.format(name=re.escape(name)), lowered) is not None
