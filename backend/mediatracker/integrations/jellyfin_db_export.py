"""Export Jellyfin watch history via the reference identification engine (stdlib)."""

from __future__ import annotations

import csv
import logging
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any

from mediatracker.integrations.jellyfin_efcore_data import (
    PLACEHOLDER_ITEM_ID,
    JellyfinExportError,
    guids_equal,
    normalize_guid,
    normalize_jellyfin_id,
    open_efcore_store,
    verify_sqlite_file,
)
from mediatracker.integrations.jellyfin_history_engine import (
    CSV_COLUMNS as ENGINE_CSV_COLUMNS,
)
from mediatracker.integrations.jellyfin_history_engine import (
    compute_stats,
    kept,
    load_raw_from_store,
    norm_guid,
    run_normalized,
)

CSV_COLUMNS = ENGINE_CSV_COLUMNS

logger = logging.getLogger(__name__)

_CORRECTION_CONFIDENCE = frozenset({"probable", "non importable"})

_read_tmdb_token = None
_resolve_exact_tmdb_candidate = None
_TmdbExportRateLimited = None


def bind_core_tmdb(*, read_tmdb_token, resolve_exact_tmdb_candidate, tmdb_export_rate_limited):
    global _read_tmdb_token, _resolve_exact_tmdb_candidate, _TmdbExportRateLimited
    _read_tmdb_token = read_tmdb_token
    _resolve_exact_tmdb_candidate = resolve_exact_tmdb_candidate
    _TmdbExportRateLimited = tmdb_export_rate_limited


def _require_tmdb_export_bindings():
    if _read_tmdb_token is None or _resolve_exact_tmdb_candidate is None or _TmdbExportRateLimited is None:
        raise RuntimeError("Jellyfin export TMDB wiring missing (wire_jellyfin_integrations)")
    return _read_tmdb_token, _resolve_exact_tmdb_candidate, _TmdbExportRateLimited


@dataclass(frozen=True)
class JellyfinUser:
    id: str
    username: str


@dataclass(frozen=True)
class WatchRow:
    type: str
    title: str
    year: int | None
    series_title: str | None
    season: int | None
    episode: int | None
    tmdb_id: int | None
    imdb_id: str | None
    tvdb_id: int | None
    jellyfin_item_id: str
    watched_at: datetime | None
    play_count: int
    source: str
    confiance: str = "sûr"
    raison: str = ""
    drapeaux: str = ""
    vu: str = "oui"
    tmdb_series_id: int | None = None
    jellyfin_ids: str = ""
    episode_title: str | None = None

    def merge_key(self) -> tuple:
        jid = normalize_jellyfin_id(self.jellyfin_item_id)
        if jid:
            return ("id", jid)
        if self.type == "episode" and self.tvdb_id is not None and self.season is not None and self.episode is not None:
            return ("episode", "tvdb", self.tvdb_id, self.season, self.episode)
        if self.type == "episode" and self.imdb_id and self.season is not None and self.episode is not None:
            return ("episode", "imdb", self.imdb_id, self.season, self.episode)
        return (
            self.type,
            self.tmdb_id,
            self.imdb_id or "",
            self.tvdb_id,
            self.season,
            self.episode,
            _norm(self.title),
            _norm(self.series_title or ""),
            self.year,
        )


@dataclass(frozen=True)
class ExportStats:
    userdata_count: int = 0
    activity_log_count: int = 0
    deleted_untitled_count: int = 0
    empty_message: str | None = None
    sure_movies: int = 0
    sure_episodes: int = 0
    probable_count: int = 0
    non_importable_count: int = 0
    merged_duplicates: int = 0
    excluded_unwatched: int = 0
    summary: dict[str, Any] | None = None


@dataclass(frozen=True)
class ExportBundle:
    rows: list[WatchRow]
    stats: ExportStats
    csv_rows: list[dict[str, str]]


def _norm(value: str) -> str:
    return " ".join(value.casefold().split())


def _parse_iso(value: object) -> datetime | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def _first_int(*values: str) -> int | None:
    for raw in values:
        if not raw:
            continue
        part = str(raw).split()[0]
        parsed = _int(part)
        if parsed is not None:
            return parsed
    return None


def _csv_row_to_watch(row: dict[str, str]) -> WatchRow:
    kind = (row.get("type") or "").strip().lower()
    if kind == "film":
        row_type = "movie"
        title = (row.get("titre_film") or "").strip() or (row.get("titre_episode") or "").strip()
        tmdb_id = _first_int(row.get("tmdb", ""))
    elif kind == "episode":
        row_type = "episode"
        title = (row.get("titre_episode") or "").strip() or (row.get("titre_film") or "").strip()
        tmdb_id = _first_int(row.get("tmdb_serie", ""), row.get("tmdb", ""))
    else:
        row_type = "unknown"
        title = (row.get("titre_film") or row.get("titre_episode") or row.get("titre_serie") or "").strip()
        tmdb_id = _first_int(row.get("tmdb", ""), row.get("tmdb_serie", ""))
    imdb = (row.get("imdb") or "").strip().split()[0] if row.get("imdb") else None
    tvdb = _first_int(row.get("tvdb", ""))
    season = _int(row.get("saison"))
    episode = _int(row.get("episode"))
    watched_at = _parse_iso(row.get("date_vue"))
    play_count = max(1, _int(row.get("lignes_fusionnees")) or 1)
    source = (row.get("source") or "").strip() or "userdata"
    jid = normalize_jellyfin_id(row.get("jellyfin_item_id")) or ""
    return WatchRow(
        type=row_type,
        title=title or "Sans titre",
        year=None,
        series_title=(row.get("titre_serie") or "").strip() or None,
        season=season,
        episode=episode,
        tmdb_id=tmdb_id,
        imdb_id=imdb,
        tvdb_id=tvdb,
        jellyfin_item_id=jid,
        watched_at=watched_at,
        play_count=play_count,
        source=source,
        confiance=(row.get("confiance") or "").strip() or "sûr",
        raison=(row.get("raison") or "").strip(),
        drapeaux=(row.get("drapeaux") or "").strip(),
        vu=(row.get("vu") or "oui").strip(),
        tmdb_series_id=_first_int(row.get("tmdb_serie", "")),
        jellyfin_ids=(row.get("jellyfin_ids") or "").strip(),
        episode_title=(row.get("titre_episode") or "").strip() or None,
    )


def _stats_from_engine(t: dict, all_csv_rows: list[dict], counter, events, username: str) -> ExportStats:
    user_rows = [r for r in all_csv_rows if r.get("utilisateur") == username]
    kept_rows = kept(user_rows)
    agg = compute_stats(t, user_rows, counter, events)
    unwatched = sum(agg.get("exclus_non_vus", {}).values())
    return ExportStats(
        userdata_count=sum(1 for r in kept_rows if "userdata" in (r.get("source") or "")),
        activity_log_count=sum(1 for r in kept_rows if r.get("source") == "journal"),
        deleted_untitled_count=agg.get("guid_detaches_non_attribues", 0),
        empty_message="Aucun élément vu trouvé pour ce compte" if not kept_rows else None,
        sure_movies=agg.get("sur_films", 0),
        sure_episodes=agg.get("sur_episodes", 0),
        probable_count=agg.get("probables", 0),
        non_importable_count=agg.get("non_importables", 0),
        merged_duplicates=agg.get("doublons", {}).get("fusionnes_meme_identite_ou_preuve_journal", 0),
        excluded_unwatched=unwatched,
        summary=agg,
    )


def _export_from_normalized(t: dict, user_id: str) -> ExportBundle:
    uid = norm_guid(user_id)
    username = t["users"].get(uid)
    if not username:
        raise JellyfinExportError("Utilisateur Jellyfin introuvable.")
    _, all_rows, counter, events = run_normalized(t)
    csv_rows = [r for r in kept(all_rows) if r.get("utilisateur") == username]
    watch_rows = [_csv_row_to_watch(r) for r in csv_rows]
    stats = _stats_from_engine(t, all_rows, counter, events, username)
    return ExportBundle(rows=watch_rows, stats=stats, csv_rows=csv_rows)


def export_history_bundle(
    *,
    jellyfin_db: Path | None = None,
    backup_zip: Path | None = None,
    user_id: str,
) -> ExportBundle:
    store = open_efcore_store(jellyfin_db=jellyfin_db, backup_zip=backup_zip)
    try:
        raw = load_raw_from_store(store)
        from mediatracker.integrations.jellyfin_history_engine import normalize

        t = normalize(raw)
        return _export_from_normalized(t, user_id)
    finally:
        store.close()


def export_history_for_user_id(
    jellyfin_db: Path,
    *,
    backup_zip: Path | None = None,
    user_id: str,
) -> list[WatchRow]:
    bundle = export_history_bundle(jellyfin_db=jellyfin_db, backup_zip=backup_zip, user_id=user_id)
    return bundle.rows


def export_history_for_user_id_with_stats(
    *,
    jellyfin_db: Path | None = None,
    backup_zip: Path | None = None,
    user_id: str,
) -> ExportBundle:
    return export_history_bundle(jellyfin_db=jellyfin_db, backup_zip=backup_zip, user_id=user_id)


def list_users(jellyfin_db: Path) -> list[JellyfinUser]:
    store = open_efcore_store(jellyfin_db=jellyfin_db)
    try:
        return [JellyfinUser(id=i, username=u) for i, u in store.list_users()]
    finally:
        store.close()


def list_users_from_session(*, jellyfin_db: Path | None, backup_zip: Path | None) -> list[JellyfinUser]:
    store = open_efcore_store(jellyfin_db=jellyfin_db, backup_zip=backup_zip)
    try:
        return [JellyfinUser(id=i, username=u) for i, u in store.list_users()]
    finally:
        store.close()


def detect_schema(conn: sqlite3.Connection) -> str:
    tables = {str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "UserData" in tables and "BaseItems" in tables:
        return "efcore"
    return "legacy"


def _open_ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)


def resolve_user_id(conn: sqlite3.Connection, schema: str, username: str | None) -> str:
    if schema == "efcore":
        rows = conn.execute("SELECT Id, Username FROM Users").fetchall()
        if not rows:
            raise JellyfinExportError("Aucun utilisateur dans jellyfin.db.")
        if username:
            wanted = username.casefold()
            for row in rows:
                if str(row["Username"] or "").casefold() == wanted:
                    return str(row["Id"])
            raise JellyfinExportError(f"Utilisateur Jellyfin introuvable : {username!r}.")
        if len(rows) == 1:
            return str(rows[0]["Id"])
        names = ", ".join(str(row["Username"]) for row in rows[:5])
        raise JellyfinExportError(f"Plusieurs utilisateurs ({names}…). Indiquez --user.")
    raise JellyfinExportError("Schéma Jellyfin non pris en charge.")


def export_history(jellyfin_db: Path, *, user: str | None = None) -> list[WatchRow]:
    verify_sqlite_file(jellyfin_db)
    with _open_ro(jellyfin_db) as jconn:
        jconn.row_factory = sqlite3.Row
        schema = detect_schema(jconn)
        if schema != "efcore":
            raise JellyfinExportError(
                "Seul le schéma jellyfin.db récent (UserData + BaseItems) est pris en charge."
            )
        user_id = resolve_user_id(jconn, schema, user)
    bundle = export_history_bundle(jellyfin_db=jellyfin_db, user_id=user_id)
    return bundle.rows


def rows_to_csv_text(rows: list[WatchRow] | None = None, *, csv_rows: list[dict[str, str]] | None = None) -> str:
    buffer = StringIO()
    if csv_rows is not None:
        writer = csv.DictWriter(buffer, fieldnames=list(ENGINE_CSV_COLUMNS))
        writer.writeheader()
        writer.writerows(csv_rows)
        return buffer.getvalue()
    if rows is None:
        rows = []
    writer = csv.DictWriter(buffer, fieldnames=list(ENGINE_CSV_COLUMNS))
    writer.writeheader()
    for row in rows:
        kind = "film" if row.type == "movie" else ("episode" if row.type == "episode" else row.type)
        writer.writerow(
            {
                "utilisateur": "",
                "type": kind,
                "titre_film": row.title if row.type == "movie" else "",
                "titre_serie": row.series_title or "",
                "saison": row.season if row.season is not None else "",
                "episode": row.episode if row.episode is not None else "",
                "titre_episode": row.episode_title or (row.title if row.type == "episode" else ""),
                "tmdb": row.tmdb_id if row.type == "movie" and row.tmdb_id is not None else "",
                "imdb": row.imdb_id or "",
                "tvdb": row.tvdb_id if row.tvdb_id is not None else "",
                "tmdb_serie": row.tmdb_series_id if row.tmdb_series_id is not None else "",
                "serie_guid": "",
                "ids_serie_alias": "",
                "jellyfin_item_id": row.jellyfin_item_id,
                "jellyfin_ids": row.jellyfin_ids,
                "date_vue": row.watched_at.isoformat().replace("+00:00", "Z") if row.watched_at else "",
                "date_derniere_lecture": "",
                "vu": row.vu,
                "source": row.source,
                "confiance": row.confiance,
                "raison": row.raison,
                "drapeaux": row.drapeaux,
                "evenements_journal": "0",
                "lignes_fusionnees": str(row.play_count),
            }
        )
    return buffer.getvalue()


def write_csv(rows: list[WatchRow], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rows_to_csv_text(rows), encoding="utf-8")


def _reimport_type_from_engine(row: dict[str, str]) -> str:
    kind = (row.get("type") or "").strip().lower()
    if kind in {"inconnu", "unknown", ""}:
        return "inconnu"
    if kind == "film":
        return "movie"
    if kind == "episode":
        return "episode"
    if (row.get("titre_serie") or "").strip() or (row.get("saison") or "").strip():
        return "episode"
    return "movie"


def engine_csv_row_to_reimport(row: dict[str, str]) -> dict[str, str]:
    """Convertit une ligne moteur (export complet) en ligne CSV réimportable."""
    row_type = _reimport_type_from_engine(row)
    if row_type == "episode":
        title = (row.get("titre_episode") or "").strip() or (row.get("titre_serie") or "").strip()
        tmdb_id = _first_int(row.get("tmdb_serie", ""), row.get("tmdb", ""))
    else:
        title = (row.get("titre_film") or "").strip() or (row.get("titre_episode") or "").strip()
        tmdb_id = _first_int(row.get("tmdb", ""))
    imdb = (row.get("imdb") or "").strip()
    if imdb:
        imdb = imdb.split()[0]
    tvdb = (row.get("tvdb") or "").strip()
    if tvdb and " " not in tvdb:
        tvdb = str(_first_int(tvdb) or "")
    jid = normalize_jellyfin_id(row.get("jellyfin_item_id")) or ""
    watched = (row.get("date_vue") or "").strip()
    year_raw = (row.get("annee") or row.get("annee_film") or row.get("annee_serie") or "").strip()
    return {
        "type": row_type,
        "title": title,
        "year": year_raw,
        "series_title": (row.get("titre_serie") or "").strip(),
        "season": (row.get("saison") or "").strip(),
        "episode": (row.get("episode") or "").strip(),
        "tmdb_id": str(tmdb_id) if tmdb_id is not None else "",
        "imdb_id": imdb,
        "tvdb_id": tvdb,
        "jellyfin_item_id": jid,
        "watched_at": watched,
        "play_count": (row.get("lignes_fusionnees") or "1").strip() or "1",
        "source": (row.get("source") or "").strip() or "userdata",
        "raison": (row.get("raison") or "").strip(),
        "drapeaux": (row.get("drapeaux") or "").strip(),
        "confiance": (row.get("confiance") or "").strip(),
        "serie_guid": (row.get("serie_guid") or "").strip(),
        "ids_serie_alias": (row.get("ids_serie_alias") or "").strip(),
    }


def watch_row_to_reimport(row: WatchRow) -> dict[str, str]:
    if row.type == "episode":
        tmdb = row.tmdb_series_id if row.tmdb_series_id is not None else row.tmdb_id
        title = row.episode_title or row.title
    else:
        tmdb = row.tmdb_id
        title = row.title
    watched = row.watched_at.isoformat().replace("+00:00", "Z") if row.watched_at else ""
    return {
        "type": row.type if row.type in {"movie", "episode"} else "movie",
        "title": title,
        "year": str(row.year) if row.year is not None else "",
        "series_title": row.series_title or "",
        "season": str(row.season) if row.season is not None else "",
        "episode": str(row.episode) if row.episode is not None else "",
        "tmdb_id": str(tmdb) if tmdb is not None else "",
        "imdb_id": row.imdb_id or "",
        "tvdb_id": str(row.tvdb_id) if row.tvdb_id is not None else "",
        "jellyfin_item_id": row.jellyfin_item_id or "",
        "watched_at": watched,
        "play_count": str(row.play_count),
        "source": row.source,
        "raison": row.raison,
        "drapeaux": row.drapeaux,
        "confiance": row.confiance,
    }


def _row_lacks_provider_ids(reimport_row: dict[str, str]) -> bool:
    return not (
        (reimport_row.get("tmdb_id") or "").strip()
        or (reimport_row.get("imdb_id") or "").strip()
        or (reimport_row.get("tvdb_id") or "").strip()
    )


def correction_csv_rows(
    bundle: ExportBundle,
    *,
    db=None,
    settings=None,
    secret_key: str | None = None,
    tmdb_cache: dict | None = None,
    search_hook=None,
    tmdb_client=None,
) -> list[dict[str, str]]:
    """Probables et non importables, prêts pour édition manuelle puis réimport CSV."""
    from mediatracker.integrations.tmdb import TmdbClient, borrow_tmdb

    out: list[dict[str, str]] = []
    cache = tmdb_cache if tmdb_cache is not None else {}
    owned = False
    client = tmdb_client
    read_tmdb_token = None
    resolve_exact_tmdb_candidate = None
    TmdbExportRateLimited = None
    stop_search = False

    def _load_tmdb_export_bindings():
        nonlocal read_tmdb_token, resolve_exact_tmdb_candidate, TmdbExportRateLimited
        if read_tmdb_token is None:
            read_tmdb_token, resolve_exact_tmdb_candidate, TmdbExportRateLimited = _require_tmdb_export_bindings()

    def _ensure_shared_client() -> None:
        nonlocal client, owned
        if client is not None or search_hook is not None:
            return
        if db is None or settings is None or not secret_key:
            return
        _load_tmdb_export_bindings()
        token = read_tmdb_token(db, secret_key)
        if token:
            client, owned = borrow_tmdb(token, TmdbClient)

    try:
        for raw in bundle.csv_rows:
            conf = (raw.get("confiance") or "").strip()
            if conf not in _CORRECTION_CONFIDENCE:
                continue
            row = engine_csv_row_to_reimport(raw)
            row.setdefault("candidat_tmdb_id", "")
            row.setdefault("candidat_titre", "")
            row.setdefault("candidat_annee", "")
            if not stop_search and conf == "probable" and _row_lacks_provider_ids(row):
                query = (row.get("series_title") or row.get("title") or "").strip()
                if query:
                    _ensure_shared_client()
                    if search_hook is not None or client is not None:
                        _load_tmdb_export_bindings()
                        media_type = "series" if row.get("type") == "episode" else "film"
                        year_raw = (row.get("year") or row.get("series_year") or "").strip()
                        year = int(year_raw) if year_raw.isdigit() else None
                        try:
                            candidate = resolve_exact_tmdb_candidate(
                                query=query,
                                media_type=media_type,
                                year=year,
                                cache=cache,
                                search_hook=search_hook,
                                client=client,
                            )
                        except TmdbExportRateLimited:
                            stop_search = True
                            candidate = None
                        if candidate is not None:
                            row["candidat_tmdb_id"] = str(candidate.tmdb_id)
                            row["candidat_titre"] = candidate.title
                            row["candidat_annee"] = str(candidate.year) if candidate.year is not None else ""
            out.append(row)
    finally:
        if owned and client is not None:
            client.close()
    return out


def rows_to_reimport_csv_text(rows: list[dict[str, str]]) -> str:
    from mediatracker.integrations.jellyfin_history_import import REIMPORT_CSV_COLUMNS

    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(REIMPORT_CSV_COLUMNS), extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({col: row.get(col, "") for col in REIMPORT_CSV_COLUMNS})
    return buffer.getvalue()


def merge_rows(rows: list[WatchRow]) -> list[WatchRow]:
    merged: dict[tuple, WatchRow] = {}
    for row in rows:
        key = row.merge_key()
        prev = merged.get(key)
        if prev is None:
            merged[key] = row
            continue
        watched_at = prev.watched_at
        if row.watched_at and (watched_at is None or row.watched_at > watched_at):
            watched_at = row.watched_at
        play_count = max(prev.play_count, row.play_count)
        merged[key] = replace(prev, watched_at=watched_at, play_count=play_count)
    return sorted(merged.values(), key=lambda r: (r.type, r.series_title or "", r.season or 0, r.episode or 0, r.title))


def count_attached_played_without_dedup(store, user_id: str) -> int:
    count = 0
    for row in store.fetch_userdata(user_id):
        played = row.get("Played")
        if played in (0, "0", False):
            continue
        item_id = normalize_guid(str(row.get("ItemId") or ""))
        if item_id and not guids_equal(item_id, PLACEHOLDER_ITEM_ID):
            count += 1
    return count


# Legacy helpers for playback_reporting tool (minimal item row builder).
def _row_from_item(
    items: dict[str, dict],
    providers: dict[str, dict[str, str]],
    item_id: str,
    *,
    watched_at: datetime | None,
    play_count: int,
    source: str,
    fallback_title: str = "",
) -> WatchRow | None:
    item = items.get(normalize_guid(item_id)) or items.get(item_id)
    if item is None:
        return None
    prov = providers.get(normalize_guid(item_id), {})
    tmdb = _int(prov.get("Tmdb"))
    imdb = prov.get("Imdb")
    tvdb = _int(prov.get("Tvdb"))
    type_name = str(item.get("Type") or item.get("type") or "")
    if "Episode" in type_name:
        row_type = "episode"
        title = str(item.get("Name") or fallback_title)
        series_title = str(item.get("SeriesName") or "")
        season = _int(item.get("ParentIndexNumber"))
        episode = _int(item.get("IndexNumber"))
    else:
        row_type = "movie"
        title = str(item.get("Name") or fallback_title)
        series_title = None
        season = None
        episode = None
    return WatchRow(
        type=row_type,
        title=title,
        year=_int(item.get("ProductionYear")),
        series_title=series_title,
        season=season,
        episode=episode,
        tmdb_id=tmdb,
        imdb_id=imdb,
        tvdb_id=tvdb,
        jellyfin_item_id=normalize_jellyfin_id(item_id),
        watched_at=watched_at,
        play_count=play_count,
        source=source,
    )
