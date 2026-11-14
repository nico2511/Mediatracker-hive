"""Compare Jellyfin library locations with MediaTracker media roots.

A path map is a list of ``préfixe Jellyfin=préfixe MediaTracker`` pairs,
separated by ``;``. The old single prefix remains a fallback.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path, PurePosixPath

_MOVIE_ALIASES = frozenset({"film", "films", "movie", "movies"})
_SERIES_ALIASES = frozenset({"serie", "series", "tv", "tvshow", "tvshows", "show", "shows"})


def parse_path_map(raw: str | None) -> list[tuple[str, str]]:
    """Parse ``/data/movies=/data/Films;/data/tvshows=/data/Séries``.

    Raises ``ValueError`` with a French message when a pair is unusable.
    """
    if raw is None or not raw.strip():
        return []
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for piece in raw.split(";"):
        item = piece.strip()
        if not item:
            continue
        if "=" not in item:
            raise ValueError(f"Correspondance invalide : « {item} ».")
        left, right = item.split("=", 1)
        source = _absolute(left, "préfixe Jellyfin")
        target = _absolute(right, "préfixe MediaTracker")
        if source in seen:
            raise ValueError(f"Correspondance en double pour {source}.")
        seen.add(source)
        pairs.append((source, target))
    return pairs


def format_path_map(pairs: list[tuple[str, str]]) -> str:
    return ";".join(f"{source}={target}" for source, target in pairs)


def translate_path(remote: str, mappings: list[tuple[str, str]]) -> str | None:
    """Apply the longest matching Jellyfin prefix. ``None`` when none matches."""
    right = _norm(remote)
    best: str | None = None
    best_len = -1
    for source, target in mappings:
        root = _norm(source).rstrip("/")
        if not root:
            continue
        if right == root or right.startswith(root + "/"):
            if len(root) > best_len:
                best = _norm(target).rstrip("/") + right[len(root) :]
                best_len = len(root)
    return best


def untranslate_path(local: str, mappings: list[tuple[str, str]]) -> str | None:
    """Longest MediaTracker prefix back onto the Jellyfin prefix. ``None`` when none matches."""
    left = _norm(local)
    best: str | None = None
    best_len = -1
    for source, target in mappings:
        root = _norm(target).rstrip("/")
        if not root:
            continue
        if left == root or left.startswith(root + "/"):
            if len(root) > best_len:
                best = _norm(source).rstrip("/") + left[len(root) :]
                best_len = len(root)
    return best


def suggest_path_map(movies_dir: Path, series_dir: Path, folders: list[dict]) -> str:
    """Propose mappings from Jellyfin virtual folders.

    A library is paired with a local directory when its contents overlap, when
    its name matches that directory, or, failing that, from the collection type
    (movies / tvshows). An already identical path is left out.
    """
    chosen: list[tuple[str, str]] = []
    seen: set[str] = set()
    for folder in folders:
        name = str(folder.get("name") or "")
        kind = str(folder.get("collection_type") or "")
        locations = [str(item) for item in folder.get("locations") or [] if item]
        for location in locations:
            try:
                source = _absolute(location, "chemin Jellyfin")
            except ValueError:
                continue
            if source in seen:
                continue
            target = _match_library(location, name, kind, movies_dir, series_dir)
            if target is None:
                continue
            if _norm(source) == _norm(str(target)):
                continue
            seen.add(source)
            chosen.append((source, str(target)))
    return format_path_map(chosen)


def library_path_warnings(
    movies_root: str,
    series_root: str,
    folders: list[dict],
    prefix: str = "",
    mappings: list[tuple[str, str]] | None = None,
) -> list[str]:
    """Warn when a Jellyfin library path would not line up with a media root.

    ``prefix`` is the existing fallback: a Jellyfin path that starts with it can
    still match a MediaTracker path that ends with the remainder. ``mappings``
    translate a Jellyfin prefix onto a MediaTracker prefix.
    """
    if not folders:
        return [
            "Jellyfin n'a renvoyé aucun chemin de bibliothèque. "
            "Vérifiez que les dossiers sont montés, ou renseignez la correspondance de chemins."
        ]
    pairs = mappings or []
    warnings: list[str] = []
    for folder in folders:
        name = str(folder.get("name") or "Bibliothèque")
        kind = str(folder.get("collection_type") or "")
        locations = [str(item) for item in folder.get("locations") or [] if item]
        if not locations:
            warnings.append(f"« {name} » n'a pas de chemin dans Jellyfin.")
            continue
        roots = _roots_for(kind, movies_root, series_root)
        if any(_covered(root, location, prefix, pairs) for root in roots for location in locations):
            continue
        shown = ", ".join(locations)
        expected = " ou ".join(roots)
        warnings.append(
            f"« {name} » pointe vers {shown}, qui ne correspond pas à {expected}. "
            "Montez le même dossier au même chemin dans les deux conteneurs, "
            "ou renseignez la correspondance de chemins (l'ancien préfixe reste un repli)."
        )
    return warnings


def _covered(local: str, remote: str, prefix: str, mappings: list[tuple[str, str]]) -> bool:
    if _aligns(local, remote, prefix):
        return True
    translated = translate_path(remote, mappings)
    if translated is None:
        return False
    return _norm(translated).rstrip("/") == _norm(local).rstrip("/")


def _roots_for(kind: str, movies_root: str, series_root: str) -> list[str]:
    if kind == "movies":
        return [movies_root]
    if kind in {"tvshows", "tv"}:
        return [series_root]
    return [movies_root, series_root]


def _aligns(local: str, remote: str, prefix: str) -> bool:
    """Exact root, or the configured prefix fallback. Not the last-three-parts guess."""
    if not local or not remote:
        return False
    left = _norm(local).rstrip("/")
    right = _norm(remote).rstrip("/")
    if left == right:
        return True
    if not prefix:
        return False
    root = _norm(prefix).rstrip("/")
    if root and (right == root or right.startswith(root + "/")):
        suffix = right[len(root) :]
        return bool(suffix) and left.endswith(suffix)
    return False


def _match_library(location: str, name: str, kind: str, movies: Path, series: Path) -> Path | None:
    overlapped = [folder for folder in (movies, series) if _contents_overlap(location, folder)]
    if len(overlapped) == 1:
        return overlapped[0]
    named = [folder for folder in (movies, series) if _same_name(name, location, folder)]
    if len(named) == 1:
        return named[0]
    if kind == "movies":
        return movies
    if kind in {"tvshows", "tv"}:
        return series
    aliased = [folder for folder in (movies, series) if _alias_hit(name, location, folder)]
    if len(aliased) == 1:
        return aliased[0]
    return None


def _contents_overlap(location: str, folder: Path) -> bool:
    remote = _child_names(Path(location))
    local = _child_names(folder)
    if not remote or not local:
        return False
    return bool(remote & local)


def _child_names(folder: Path) -> set[str] | None:
    if not folder.is_dir():
        return None
    try:
        names = {entry.name.casefold() for entry in folder.iterdir() if not entry.name.startswith(".")}
    except OSError:
        return None
    return names or None


def _same_name(library_name: str, location: str, folder: Path) -> bool:
    local = _fold(folder.name)
    if not local:
        return False
    base = _fold(PurePosixPath(location.replace("\\", "/")).name)
    return _fold(library_name) == local or base == local


def _alias_hit(library_name: str, location: str, folder: Path) -> bool:
    folded = _fold(folder.name)
    if folded in _MOVIE_ALIASES:
        aliases = _MOVIE_ALIASES
    elif folded in _SERIES_ALIASES:
        aliases = _SERIES_ALIASES
    else:
        return False
    tokens = {_fold(library_name), _fold(PurePosixPath(location.replace("\\", "/")).name)}
    tokens.discard("")
    return bool(tokens & aliases)


def _absolute(value: str, label: str) -> str:
    raw = value.strip()
    if "\x00" in raw or len(raw) > 500:
        raise ValueError(f"Le {label} est illisible.")
    text = _norm(raw)
    if not text.startswith("/"):
        raise ValueError(f"Le {label} doit être un chemin absolu.")
    if ".." in PurePosixPath(text).parts:
        raise ValueError(f"Le {label} n'est pas utilisable.")
    return text


def _norm(value: str) -> str:
    text = unicodedata.normalize("NFC", value.replace("\\", "/")).rstrip("/")
    while "//" in text:
        text = text.replace("//", "/")
    return text


def _fold(value: str) -> str:
    text = unicodedata.normalize("NFKD", value)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.casefold().replace("œ", "oe").replace("æ", "ae")
    return "".join(char for char in text if char.isalnum())
