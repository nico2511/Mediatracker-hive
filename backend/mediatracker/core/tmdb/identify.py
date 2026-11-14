"""Match TMDB search hits while scanning the local library."""

from dataclasses import dataclass

from mediatracker.library.parse import ParsedName, normalize_title


@dataclass(frozen=True)
class IdentifyResult:
    hit: object | None = None
    candidates: tuple[dict, ...] = ()
    pending: bool = False
    no_results: bool = False


def glance_hit(tmdb, hit):
    try:
        detailed = tmdb.glance(hit.tmdb_id, hit.media_type)
    except Exception:
        return hit
    return detailed or hit


def candidate_payload(hit) -> dict:
    from mediatracker.core.library_plan import _suggestion

    item = _suggestion(hit, getattr(hit, "media_type", None) or "series")
    return {
        "tmdb_id": item.tmdb_id,
        "title": item.title,
        "original_title": item.original_title,
        "year": item.year,
        "media_type": item.media_type,
        "poster_url": item.poster_url,
        "overview": item.overview,
        "season_count": item.season_count,
        "episode_count": item.episode_count,
        "runtime": item.runtime,
    }


def hit_years_differ(hits, parsed: ParsedName) -> bool:
    wanted = normalize_title(parsed.title)
    years = {hit.year for hit in hits if normalize_title(hit.title) == wanted}
    return len(years) > 1


def exact_hit(hits, parsed: ParsedName):
    wanted = normalize_title(parsed.title)
    if not wanted or wanted == "inconnu":
        return None
    exact = [
        hit
        for hit in hits
        if normalize_title(hit.title) == wanted and parsed.year is not None and hit.year == parsed.year
    ]
    if len(exact) == 1:
        return exact[0]
    titled = [hit for hit in hits if normalize_title(hit.title) == wanted]
    if parsed.year is None and len({hit.tmdb_id for hit in titled}) == 1 and len(titled) >= 1:
        return titled[0]
    return None


def identify(
    hits,
    parsed: ParsedName,
    *,
    tmdb=None,
    max_candidates: int = 5,
    with_glance: bool = True,
) -> IdentifyResult:
    """One identification rule: exact title+year match, else pending with candidates."""
    exact = exact_hit(hits, parsed)
    if exact is not None:
        return IdentifyResult(hit=exact)
    if not hits:
        return IdentifyResult(no_results=True)
    pool = list(hits[:max_candidates])
    if with_glance and tmdb is not None and hasattr(tmdb, "glance"):
        pool = [glance_hit(tmdb, hit) for hit in pool]
    return IdentifyResult(candidates=tuple(candidate_payload(hit) for hit in pool), pending=True)


def identify_search(
    tmdb,
    title: str,
    year: int | None,
    media_type: str,
    *,
    max_candidates: int = 5,
    with_glance: bool = True,
) -> IdentifyResult:
    parsed = ParsedName(
        title=title,
        year=year,
        season=None,
        episode=None,
        tmdb_id=None,
        quality=None,
        language=None,
        media_type=media_type,
    )
    if tmdb is None or not (title or "").strip():
        return IdentifyResult(no_results=True)
    from mediatracker.integrations.tmdb import TmdbError

    try:
        hits = tmdb.search(title, media_type)
    except TmdbError as exc:
        if exc.code == "RATE_LIMIT":
            raise
        hits = []
    except Exception:
        hits = []
    return identify(hits, parsed, tmdb=tmdb, max_candidates=max_candidates, with_glance=with_glance)
