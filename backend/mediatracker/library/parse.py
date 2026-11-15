"""Guessit-like names for a local library.

Covers Jellyfin folders, ``Show S01E02``, ``Show.1x02``, release tokens, and a
``[tmdbid-123]`` tag. It does not pull in the guessit package.
"""

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

VIDEO_EXT = frozenset({".mkv", ".mp4", ".avi", ".m4v", ".ts", ".webm", ".mov"})

_TMDB = re.compile(r"[\[{(]tmdbid-(\d+)[\]})]", re.IGNORECASE)
# S01E01, S01E01-E02, S01E01-02, S01E01E02. Season 00 is specials.
_SEASON_EPISODE = re.compile(
    r"(?i)(?:^|[^a-z0-9])S(?P<season>\d{1,2})[ ._-]*E(?P<episode>\d{1,3})"
    r"(?:"
    r"[ ._-]*-[ ._-]*E(?P<end_e>\d{1,3})"
    r"|[ ._-]*-[ ._-]*(?P<end_n>\d{2,3})"
    r"|(?:[ ._-]*E(?P<end_glue>\d{1,3}))"
    r")?"
    r"(?![0-9])"
)
_X_EPISODE = re.compile(r"(?<!\d)(\d{1,2})x(\d{2})(?!\d)")
_FRENCH_EPISODE = re.compile(
    r"(?i)(?:^|[^a-z0-9])(?:saison|season)\s*0*(?P<season>\d{1,2})\D{0,32}?"
    r"(?:episode|épisode|ep\.?)\s*(?P<episode>\d{1,3})(?![0-9])"
)
_SEASON_RANGE = re.compile(
    r"(?i)(?:^|[^a-z0-9])s(?P<s1>\d{1,2})\s*-\s*s(?P<s2>\d{1,2})(?![0-9])"
)
_SEASON_ONLY = re.compile(r"(?i)(?:^|[^a-z0-9])s(?P<season>\d{1,2})(?![0-9e])")
_SEASON_WORD = re.compile(
    r"(?i)(?:^|[^a-z0-9])(?:saison|season)\s*0*(?P<season>\d{1,2})(?![0-9])"
    r"(?!\s*0*(?:episode|épisode|ep\.?)\s*\d)"
)
_EPISODE_ONLY_LABEL = re.compile(
    r"(?i)(?:^|[^a-z0-9])(?:episode|épisode|ep\.?)\s*(?P<episode>\d{1,3})(?![0-9])"
)
_YEAR = re.compile(r"(?:^|[^0-9])((?:19|20)\d{2})(?:[^0-9]|$)")
_SEASON_NUMBERED = re.compile(r"(?i)^(?:season|saison)\s*0*(\d{1,2})$")
_SPECIALS_DIR = re.compile(r"(?i)^(?:specials|spéciaux|speciaux)$")
_FOLDER = re.compile(r"^(?P<title>.+?)(?: \((?P<year>(?:19|20)\d{2})\))?$")
_EPISODE_ONLY = re.compile(r"(?i)^e(\d{1,3})$")
_QUALITY = (
    (re.compile(r"(?i)(?:2160p|4k)\b"), "2160p"),
    (re.compile(r"(?i)1080p\b"), "1080p"),
    (re.compile(r"(?i)720p\b"), "720p"),
    (re.compile(r"(?i)480p\b"), "480p"),
)
_LANGUAGE = (
    (re.compile(r"(?i)(?:^|[^a-z])vostfr(?:[^a-z]|$)"), "VOSTFR"),
    (re.compile(r"(?i)(?:^|[^a-z])vff(?:[^a-z]|$)"), "VFF"),
    (re.compile(r"(?i)(?:^|[^a-z])vfq(?:[^a-z]|$)"), "VFQ"),
    (re.compile(r"(?i)(?:^|[^a-z])multi(?:[^a-z]|$)"), "MULTI"),
    (
        re.compile(
            r"(?i)(?:multi|vff|vfq|vf2|truefrench|french|vostfr|vf)[.\s_\-()]*ca(?:[^a-z]|$)"
            r"|(?:^|[^a-z])ca[.\s_\-()]*(?:multi|vff|vfq|vf2|truefrench|french|vostfr|vf)(?:[^a-z]|$)"
        ),
        "VFQ",
    ),
    (re.compile(r"(?i)(?:^|[^a-z])vf2(?:[^a-z]|$)"), "VFF"),
    (re.compile(r"(?i)(?:^|[^a-z])vf(?:[^a-z]|$)"), "VF"),
)
_NOISE = re.compile(
    r"(?i)\b(?:2160p|1080p|720p|480p|4k|bluray|blu-ray|bdrip|brrip|webrip|web-dl|webdl|web|"
    r"hdtv|hdrip|x264|x265|h\.?264|h\.?265|hevc|aac|ac3|eac3|dts|truehd|atmos|hdr|dv|"
    r"10bit|8bit|proper|repack|extended|remux|vostfr|vff|vf2|vfq|multi|vf|vo|sample|"
    r"5[.\s]1|7[.\s]1|2[.\s]0)\b"
)
_CONTEXTUAL_CA_STRIP = re.compile(
    r"(?i)(?:multi|vff|vfq|vf2|truefrench|french|vostfr|vf)[.\s_\-()]*ca(?:[^a-z0-9]|$)"
)
_LANG_MARKER = frozenset({"multi", "vff", "vfq", "vf2", "truefrench", "french", "vostfr", "vf"})
_SAMPLE = re.compile(r"(?i)(?:^|[._ -])sample(?:[._ -]|$)")
# Release words glued to an episode. They are not part of the title and must not hide SxxExx.
_EPISODE_TAG = re.compile(
    r"(?i)\b(?:final|proper|repack|dirfix|rerip|internal|nuked|limited|subbed|dubbed)\b"
)


@dataclass(frozen=True)
class ParsedName:
    title: str
    year: int | None
    season: int | None
    episode: int | None
    tmdb_id: int | None
    quality: str | None
    language: str | None
    media_type: str
    episode_end: int | None = None
    season_end: int | None = None
    season_pack: bool = False


@dataclass(frozen=True)
class _SlotMatch:
    season: int | None = None
    episode: int | None = None
    episode_end: int | None = None
    season_end: int | None = None
    pack: bool = False


def slot_from_name(text: str) -> tuple[int | None, int | None]:
    """Season and episode from a filename or a pasted URL. Ambiguous names stay empty."""
    found = _slot(text or "")
    if found.episode is None:
        return None, None
    return found.season, found.episode


def parse_release_slot(text: str) -> _SlotMatch:
    """Full slot metadata for releases (packs, ranges, multi-episode). ``slot_from_name`` skips packs."""
    return _slot(text or "")


def episode_bits_from_text(text: str) -> tuple[int | None, int | None]:
    """Season and episode from a title line (search hits, page headings)."""
    found = _slot(text or "")
    if found.season is not None or found.episode is not None:
        return found.season, found.episode
    label = _EPISODE_ONLY_LABEL.search(text or "")
    if label is not None:
        return None, int(label.group("episode"))
    return None, None


def release_matches_episode(text: str, *, season: int, episode: int) -> bool:
    """Whether a release name targets this season/episode (incl. multi-episode and season packs)."""
    found = _slot(text or "")
    if found.season is None and found.episode is None:
        return False
    if not _season_covers(found, season):
        return False
    if found.pack and found.episode is None:
        return True
    if found.episode == episode:
        return True
    if (
        found.episode is not None
        and found.episode_end is not None
        and found.episode <= episode <= found.episode_end
    ):
        return True
    return False


def is_video(path: Path) -> bool:
    if path.suffix.lower() not in VIDEO_EXT:
        return False
    return _SAMPLE.search(path.name) is None


def parse_path(path: Path, *, root: Path, root_kind: str) -> ParsedName:
    stem = path.stem
    file_tmdb = _tmdb(stem)
    folder_tmdb = _tmdb_above(path, root)
    slot = _slot(stem)
    season, episode, episode_end = slot.season, slot.episode, slot.episode_end
    season_end, season_pack = slot.season_end, slot.pack
    if season is None:
        season, episode = _slot_from_parents(path, root)
        episode_end = None
        season_end = None
        season_pack = False
    stem_year = _year(stem)
    quality = _quality(stem)
    language = _language(stem)
    folder = _folder_title(path, root)
    stem_title = _clean_stem(stem)
    # A file that names another show does not inherit the folder's TMDB tag.
    if folder is not None and _named_release(stem_title) and not _same_title(stem_title, folder[0]):
        title = stem_title
        year = stem_year
        tmdb_id = file_tmdb
    elif folder is not None:
        title, folder_year = folder
        # The Jellyfin folder year identifies the title when the filename carries another year.
        if folder_year is not None and stem_year is not None and folder_year != stem_year:
            year = folder_year
        else:
            year = stem_year or folder_year
        tmdb_id = file_tmdb or folder_tmdb
    else:
        title = stem_title
        year = stem_year
        tmdb_id = file_tmdb or folder_tmdb
    media_type = (
        "series" if season is not None and (episode is not None or season_pack) else root_kind
    )
    return ParsedName(
        title=title[:300],
        year=year,
        season=season,
        episode=episode,
        tmdb_id=tmdb_id,
        quality=quality,
        language=language,
        media_type=media_type if media_type in {"film", "series"} else "film",
        episode_end=episode_end,
        season_end=season_end,
        season_pack=season_pack,
    )


def parse_release(filename: str) -> ParsedName:
    """Series name from the filename only. The folder is not consulted."""
    stem = Path(filename).stem
    slot = _slot(stem)
    season, episode = slot.season, slot.episode
    media_type = "series" if season is not None and (episode is not None or slot.pack) else "film"
    if season is not None and episode is None and not slot.pack:
        media_type = "film"
    return ParsedName(
        title=_clean_stem(stem)[:300],
        year=_year(stem),
        season=season,
        episode=episode,
        tmdb_id=_tmdb(stem),
        quality=_quality(stem),
        language=_language(stem),
        media_type=media_type,
        episode_end=slot.episode_end,
        season_end=slot.season_end,
        season_pack=slot.pack,
    )


def show_key(text: str) -> str:
    """Comparable show title. Episode codes and release tags do not count."""
    raw = (text or "").replace("'", "").replace("’", "").replace("`", "")
    raw = raw.replace(".", " ").replace("_", " ").replace("-", " ")
    tagged = bool(
        _SEASON_EPISODE.search(text or "")
        or _X_EPISODE.search(text or "")
        or _FRENCH_EPISODE.search(text or "")
    )
    raw = _SEASON_EPISODE.sub(" ", raw)
    raw = _X_EPISODE.sub(" ", raw)
    raw = _FRENCH_EPISODE.sub(" ", raw)
    raw = _SEASON_RANGE.sub(" ", raw)
    raw = _SEASON_ONLY.sub(" ", raw)
    raw = _YEAR.sub(" ", raw)
    raw = _NOISE.sub(" ", raw)
    if tagged:
        raw = _EPISODE_TAG.sub(" ", raw)
    raw = re.sub(r"(?i)\b(?:saison|season)\s*\d+\b", " ", raw)
    return normalize_title(raw)


def normalize_title(title: str) -> str:
    text = unicodedata.normalize("NFKD", title)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^a-zA-Z0-9]+", " ", text).strip().lower()
    return text


def _tmdb(text: str) -> int | None:
    match = _TMDB.search(text)
    if match is None:
        return None
    return int(match.group(1))


def _tmdb_above(path: Path, root: Path) -> int | None:
    """TMDB tag on a parent folder, including the series folder above ``Season NN``."""
    try:
        relative = path.parent.relative_to(root)
    except ValueError:
        return None
    for part in relative.parts:
        found = _tmdb(part)
        if found is not None:
            return found
    return None


def _same_title(left: str, right: str) -> bool:
    folded = normalize_title(left)
    return folded != "" and folded != "inconnu" and folded == normalize_title(right)


def _named_release(title: str) -> bool:
    folded = normalize_title(title)
    if not folded or folded in {"inconnu", "episode", "video", "sample", "movie", "film"}:
        return False
    compact = folded.replace(" ", "")
    if re.fullmatch(r"e?\d+", compact):
        return False
    return sum(char.isalpha() for char in folded) >= 3


def _season_covers(found: _SlotMatch, season: int) -> bool:
    if found.season is None:
        return False
    if found.season == season:
        return True
    if found.pack and found.season_end is not None:
        return found.season <= season <= found.season_end
    return False


def _slot(stem: str) -> _SlotMatch:
    text = stem or ""
    match = _SEASON_EPISODE.search(text)
    if match:
        season = int(match.group("season"))
        episode = int(match.group("episode"))
        end_raw = match.group("end_e") or match.group("end_n") or match.group("end_glue")
        end = int(end_raw) if end_raw else None
        if end is not None and end <= episode:
            end = None
        return _SlotMatch(season, episode, end)
    match = _X_EPISODE.search(text)
    if match:
        return _SlotMatch(int(match.group(1)), int(match.group(2)))
    match = _FRENCH_EPISODE.search(text)
    if match:
        return _SlotMatch(int(match.group("season")), int(match.group("episode")))
    match = _SEASON_RANGE.search(text)
    if match:
        first = int(match.group("s1"))
        last = int(match.group("s2"))
        if last >= first:
            return _SlotMatch(first, None, season_end=last, pack=True)
    match = _SEASON_WORD.search(text)
    if match:
        return _SlotMatch(int(match.group("season")), None, pack=True)
    match = _SEASON_ONLY.search(text)
    if match:
        return _SlotMatch(int(match.group("season")), None, pack=True)
    return _SlotMatch()


def season_number(part: str) -> int | None:
    if _SPECIALS_DIR.match(part):
        return 0
    match = _SEASON_NUMBERED.match(part)
    if match:
        return int(match.group(1))
    return None


def _slot_from_parents(path: Path, root: Path) -> tuple[int | None, int | None]:
    try:
        relative = path.parent.relative_to(root)
    except ValueError:
        return None, None
    season = None
    for part in relative.parts:
        number = season_number(part)
        if number is not None:
            season = number
    if season is None:
        return None, None
    episode_match = _EPISODE_ONLY.match(path.stem)
    if episode_match is None:
        return None, None
    return season, int(episode_match.group(1))


def _year(text: str) -> int | None:
    match = _YEAR.search(text)
    if match is None:
        return None
    return int(match.group(1))


def _quality(text: str) -> str | None:
    for pattern, label in _QUALITY:
        if pattern.search(text):
            return label
    return None


def _language(text: str) -> str | None:
    for pattern, label in _LANGUAGE:
        if pattern.search(text):
            return label
    return None


_WAWACITY = re.compile(r"(?i)\bwawacity\b(?:\s+[A-Za-z0-9][\w.-]{0,30})?")
_TRAILING_JUNK = re.compile(
    r"(?i)^(?:"
    r"french|truefrench|vostfr|vff|vfq|multi|vf2|vf|vo|"
    r"ddp\d*|dd\d*|eac3|atmos|aac\d*|ac3|dts|truehd|"
    r"webrip|webdl|web|bluray|bdrip|brrip|hdtv|hdrip|hdr|dv|"
    r"x264|x265|h264|h265|hevc|10bit|8bit|"
    r"1080p|720p|2160p|480p|4k|proper|repack|extended|remux"
    r")$"
)


def strip_release_junk(text: str) -> str:
    """Drop a trailing ``Wawacity <hoster>`` and the scene tokens glued in front of it.

    ``Détective Démo FRENCH DDP5 1 BraD Wawacity town`` is the series Détective Démo. The hoster
    suffix (town, surf, irish, …) changes per mirror, so each copy must not
    become its own series folder. Titles that never mention Wawacity stay whole.
    """
    raw = re.sub(r"\s+", " ", (text or "").replace(".", " ").replace("_", " ")).strip()
    if not re.search(r"(?i)wawacity", raw):
        return raw
    cleaned = _WAWACITY.sub(" ", raw)
    tokens = cleaned.split()
    while tokens:
        last = tokens[-1]
        prev = tokens[-2] if len(tokens) > 1 else ""
        if re.fullmatch(r"\d{1,2}", last) and re.fullmatch(r"(?i)ddp\d*|dd\d*|eac3|ac3", prev):
            tokens.pop()
            continue
        if last.casefold() == "ca" and prev.casefold() in _LANG_MARKER:
            tokens.pop()
            continue
        if _TRAILING_JUNK.match(last) or _scene_group(last):
            tokens.pop()
            continue
        break
    return " ".join(tokens).strip()


def _scene_group(token: str) -> bool:
    """Scene tags such as BraD, TFA, GL0P, HiggsBoson, SUPPLY. Not 'Dragon'."""
    if len(token) < 2 or len(token) > 20 or not token.isalnum():
        return False
    if token.isupper():
        return True
    if any(char.isdigit() for char in token):
        return True
    return any(char.isupper() for char in token[1:]) and any(char.islower() for char in token)


def _folder_title(path: Path, root: Path) -> tuple[str, int | None] | None:
    try:
        relative = path.parent.relative_to(root)
    except ValueError:
        return None
    parts = [part for part in relative.parts if season_number(part) is None]
    if not parts:
        return None
    raw = re.sub(r"\s+", " ", _TMDB.sub(" ", parts[0])).strip()
    match = _FOLDER.match(raw)
    if match is None:
        return None
    title = strip_release_junk(match.group("title").strip()) or match.group("title").strip()
    if not title or title.lower() in {"films", "series", "movies", "tv"}:
        return None
    year = int(match.group("year")) if match.group("year") else None
    return title, year


def _clean_stem(stem: str) -> str:
    text = stem
    if re.search(r"(?i)(1080p|720p|2160p|480p|web-?dl|bluray|x26[45])", text):
        text = re.sub(r"-[A-Za-z0-9]{2,}$", "", text)
    text = _TMDB.sub(" ", text)
    text = _SEASON_EPISODE.sub(" ", text)
    text = _X_EPISODE.sub(" ", text)
    text = _FRENCH_EPISODE.sub(" ", text)
    text = _SEASON_RANGE.sub(" ", text)
    text = _SEASON_ONLY.sub(" ", text)
    text = text.replace(".", " ").replace("_", " ")
    had_episode = bool(
        _SEASON_EPISODE.search(stem) or _X_EPISODE.search(stem) or _FRENCH_EPISODE.search(stem)
    )
    text = _YEAR.sub(" ", text)
    text = _CONTEXTUAL_CA_STRIP.sub(" ", text)
    text = _NOISE.sub(" ", text)
    if had_episode:
        text = _EPISODE_TAG.sub(" ", text)
    text = re.sub(r"[-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -")
    text = strip_release_junk(text)
    return text or "Inconnu"
