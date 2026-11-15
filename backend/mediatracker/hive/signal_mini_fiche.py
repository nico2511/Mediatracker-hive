"""Mini-fiche anonyme jointe au signal cherche (hors clé de déduplication)."""

from __future__ import annotations

import json
import re
from typing import Any

from mediatracker.db.models import Media, MediaItem
from mediatracker.hive.catalog import TMDB_POSTER_PREFIX

MINI_FICHE_TITLE_MAX = 120
MINI_FICHE_PATH_MAX = 160

_SIZE_PATH_PREFIX = re.compile(r"^/t/p/[^/]+")


def _bare_poster_path(text: str) -> str | None:
    """Chemin TMDB nu sans préfixe /t/p/<taille> (ex. /affiche.jpg)."""
    path = text.strip()
    if not path:
        return None
    if path.startswith("http://") or path.startswith("https://"):
        match = re.search(r"/t/p/[^/]+(/.*)$", path)
        if not match:
            return None
        path = match.group(1)
    elif path.startswith(TMDB_POSTER_PREFIX):
        rest = path[len(TMDB_POSTER_PREFIX) :].lstrip("/")
        path = f"/{rest}" if rest else ""
    if not path.startswith("/"):
        path = f"/{path}"
    path = _SIZE_PATH_PREFIX.sub("", path, count=1)
    if not path.startswith("/"):
        path = f"/{path}"
    return path[:MINI_FICHE_PATH_MAX] if path != "/" else None


def _poster_path_only(url: str | None) -> str | None:
    if not url or not isinstance(url, str):
        return None
    return _bare_poster_path(url)


def build_cherche_mini_fiche(media: Media, item: MediaItem) -> dict[str, Any]:
    """Construit une mini-fiche locale (titre tronqué, pas de résumé)."""
    del item
    title = (media.title or "").strip()
    if not title and media.tmdb_id is not None:
        title = f"Fiche {media.tmdb_id}"
    title = title[:MINI_FICHE_TITLE_MAX]
    doc: dict[str, Any] = {"title": title, "type": media.type}
    if media.year is not None:
        doc["year"] = int(media.year)
    path = _poster_path_only(media.poster_url)
    if path:
        doc["poster_path"] = path
    if media.type == "series":
        doc["series_title"] = title
    return doc


def sanitize_mini_fiche(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    out: dict[str, Any] = {}
    title = raw.get("title")
    if isinstance(title, str) and title.strip():
        out["title"] = title.strip()[:MINI_FICHE_TITLE_MAX]
    series_title = raw.get("series_title")
    if isinstance(series_title, str) and series_title.strip():
        out["series_title"] = series_title.strip()[:MINI_FICHE_TITLE_MAX]
    year = raw.get("year")
    if isinstance(year, int) and 1800 <= year <= 2200:
        out["year"] = year
    poster_path = raw.get("poster_path")
    if isinstance(poster_path, str) and poster_path.strip():
        bare = _bare_poster_path(poster_path)
        if bare:
            out["poster_path"] = bare
    media_type = raw.get("type")
    if media_type in {"film", "series", "movie"}:
        out["type"] = "film" if media_type == "movie" else media_type
    if not out.get("title") and not out.get("series_title"):
        return None
    return out


def parse_stored_mini_fiche(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        return None
    return sanitize_mini_fiche(raw)


def mini_fiche_display_meta(mini: dict[str, Any], *, media_type: str) -> dict[str, Any]:
    from mediatracker.integrations.tmdb import poster_url as tmdb_poster_url

    title = mini.get("title") if media_type == "film" else mini.get("series_title") or mini.get("title")
    if not isinstance(title, str) or not title.strip():
        title = mini.get("title") if isinstance(mini.get("title"), str) else "Titre inconnu"
    poster_url = None
    poster_path = mini.get("poster_path")
    if isinstance(poster_path, str) and poster_path.strip():
        bare = _bare_poster_path(poster_path)
        if bare:
            poster_url = tmdb_poster_url(bare)
    year = mini.get("year") if isinstance(mini.get("year"), int) else None
    return {"title": title.strip(), "year": year, "poster_url": poster_url}