"""Import Jellyfin watch history from CSV exported by tools/jellyfin_history_export.py."""

from __future__ import annotations

import csv
import io
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.api.errors import Problem
from mediatracker.db.models import Media, MediaItem, Setting
from mediatracker.db.session import commit_retry
from mediatracker.events import record_event
from mediatracker.integrations.jellyfin import JellyfinWatch
from mediatracker.integrations.jellyfin_db_export import WatchRow
from mediatracker.integrations.jellyfin_efcore_data import normalize_jellyfin_id
from mediatracker.integrations.jellyfin_sync import _identity_matches, match_media_items

FOLLOW_ORIGIN = "jellyfin_import"
FOLLOW_CANDIDATES_KEY = "jellyfin_history.follow_candidates"
IDENTIFY_PENDING_SUMMARY = "À identifier TMDB."
IMPORT_COMMIT_BATCH = 100

_read_tmdb_token = None
_create_from_tmdb = None
_fetch_title = None
_identify = None


def bind_core_tmdb(*, read_tmdb_token, create_from_tmdb, fetch_title, identify):
    global _read_tmdb_token, _create_from_tmdb, _fetch_title, _identify
    _read_tmdb_token = read_tmdb_token
    _create_from_tmdb = create_from_tmdb
    _fetch_title = fetch_title
    _identify = identify


def _token_reader():
    if _read_tmdb_token is None:
        raise RuntimeError("Jellyfin import TMDB wiring missing (wire_jellyfin_integrations)")
    return _read_tmdb_token
IMPORT_COMMIT_MAX_INTERVAL_S = 2.0
PARTIAL_IMPORT_HINT = (
    "Import interrompu : les lignes déjà enregistrées restent en base. "
    "Renvoyez le même fichier CSV ou la base Jellyfin pour reprendre sans doublons."
)
logger = logging.getLogger(__name__)
CSV_COLUMNS = (
    "type",
    "title",
    "year",
    "series_title",
    "season",
    "episode",
    "tmdb_id",
    "imdb_id",
    "tvdb_id",
    "jellyfin_item_id",
    "watched_at",
    "play_count",
    "source",
)

# Colonnes informatives présentes dans le CSV de correction (ignorées à la relecture).
CSV_INFO_COLUMNS = (
    "raison",
    "drapeaux",
    "confiance",
    "candidat_tmdb_id",
    "candidat_titre",
    "candidat_annee",
    "serie_guid",
    "ids_serie_alias",
)

REIMPORT_CSV_COLUMNS = CSV_COLUMNS + CSV_INFO_COLUMNS


@dataclass(frozen=True)
class HistoryLine:
    line_no: int
    row_type: str
    title: str
    year: int | None
    series_title: str | None
    season: int | None
    episode: int | None
    tmdb_id: int | None
    imdb_id: str | None
    tvdb_id: int | None
    jellyfin_item_id: str | None
    watched_at: datetime | None
    play_count: int
    source: str
    series_year: int | None = None


@dataclass
class ImportTmdbResolution:
    tmdb_id: int | None = None
    fetched: Any = None
    candidates: list[dict[str, Any]] | None = None
    identify_status: str | None = None


TMDB_IDENTIFY_PENDING = "pending"
TMDB_IDENTIFY_NO_TOKEN = "no_token"
TMDB_IDENTIFY_NO_RESULTS = "no_results"


@dataclass
class LineOutcome:
    line: HistoryLine
    status: str
    reason: str | None = None
    media_id: int | None = None
    media_title: str | None = None
    item_id: int | None = None
    create_media: bool = False


@dataclass
class ImportPreview:
    line_count: int
    matched: list[LineOutcome] = field(default_factory=list)
    to_create: list[LineOutcome] = field(default_factory=list)
    unmatched: list[LineOutcome] = field(default_factory=list)
    skipped: list[LineOutcome] = field(default_factory=list)
    identify_pending_count: int = 0
    identify_no_token_count: int = 0
    identify_no_results_count: int = 0


@dataclass
class ImportResult:
    preview: ImportPreview
    applied: int = 0
    created_media: int = 0
    already_ok: int = 0
    commits_persisted: int = 0
    follow_candidates: list[dict[str, Any]] = field(default_factory=list)


def _normalize_import_row_type(raw_type: str, *, title: str, series_title: str | None, season, episode) -> str | None:
    del title, series_title, season, episode
    kind = (raw_type or "").strip().lower()
    if kind in {"movie", "film"}:
        return "movie"
    if kind == "episode":
        return "episode"
    if kind in {"inconnu", "unknown", ""}:
        return None
    return None


def parse_csv_text(text: str) -> list[HistoryLine]:
    if not text.strip():
        raise Problem(422, "Requête invalide", "Le fichier CSV est vide.")
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise Problem(422, "Requête invalide", "En-tête CSV manquant.")
    fields = {name.strip() for name in reader.fieldnames if name}
    missing = [name for name in CSV_COLUMNS if name not in fields]
    if missing:
        raise Problem(422, "Requête invalide", f"Colonnes CSV manquantes : {', '.join(missing)}.")
    lines: list[HistoryLine] = []
    for index, raw in enumerate(reader, start=2):
        title = (raw.get("title") or "").strip()
        series_title = _opt_text(raw.get("series_title"))
        season = _opt_int(raw.get("season"))
        episode = _opt_int(raw.get("episode"))
        row_type = _normalize_import_row_type(
            raw.get("type") or "",
            title=title,
            series_title=series_title,
            season=season,
            episode=episode,
        )
        if row_type is None:
            continue
        watched_raw = raw.get("watched_at")
        if not (watched_raw or "").strip():
            watched_raw = raw.get("date_vue")
        lines.append(
            HistoryLine(
                line_no=index,
                row_type=row_type,
                title=title or "Sans titre",
                year=_opt_int(raw.get("year")),
                series_title=series_title,
                season=season,
                episode=episode,
                tmdb_id=_opt_int(raw.get("tmdb_id")),
                imdb_id=_opt_text(raw.get("imdb_id")),
                tvdb_id=_opt_int(raw.get("tvdb_id")),
                jellyfin_item_id=_opt_text(raw.get("jellyfin_item_id")),
                watched_at=_opt_dt(watched_raw),
                play_count=max(1, _opt_int(raw.get("play_count")) or 1),
                source=(raw.get("source") or "").strip() or "userdata",
                series_year=_opt_int(raw.get("series_year")),
            )
        )
    if not lines:
        raise Problem(422, "Requête invalide", "Aucune ligne de données dans le CSV.")
    return lines


def history_lines_from_watch_rows(rows: list[WatchRow], *, include_probable: bool = False) -> list[HistoryLine]:
    lines: list[HistoryLine] = []
    for index, row in enumerate(rows, start=2):
        if row.confiance == "non importable":
            continue
        if row.confiance == "probable" and not include_probable:
            continue
        if row.vu == "non":
            continue
        jid = normalize_jellyfin_id(row.jellyfin_item_id) or None
        lines.append(
            HistoryLine(
                line_no=index,
                row_type=row.type,
                title=row.title,
                year=row.year,
                series_title=row.series_title,
                season=row.season,
                episode=row.episode,
                tmdb_id=row.tmdb_id,
                imdb_id=row.imdb_id,
                tvdb_id=row.tvdb_id,
                jellyfin_item_id=jid,
                watched_at=row.watched_at,
                play_count=row.play_count,
                source=row.source,
            )
        )
    return lines


def preview_import(
    db: Session,
    lines: list[HistoryLine],
    *,
    on_progress: Callable[[int, int], None] | None = None,
    settings=None,
    secret_key: str | None = None,
) -> ImportPreview:
    preview = ImportPreview(line_count=len(lines))
    total = len(lines)
    for index, line in enumerate(lines, start=1):
        outcome = _resolve_line(db, line)
        if outcome.status == "matched":
            preview.matched.append(outcome)
        elif outcome.status == "create":
            preview.to_create.append(outcome)
        elif outcome.status == "skipped":
            preview.skipped.append(outcome)
        else:
            preview.unmatched.append(outcome)
        if on_progress is not None:
            on_progress(index, total)
    if settings is not None and secret_key is not None:
        _fill_identify_counts(db, secret_key, preview)
    return preview


def _identify_preview_media_key(line: HistoryLine) -> tuple:
    if line.row_type == "episode":
        title = _norm_title(line.series_title or "")
        return ("series", title)
    return ("film", _norm_title(line.title), line.year)


def _existing_media_for_identify(db: Session, line: HistoryLine) -> bool:
    """True si une fiche existe déjà pour cette ligne (rattachement, pas création)."""
    if line.row_type == "episode":
        title = (line.series_title or "").strip()
        if not title:
            return False
        return (
            db.scalar(select(Media).where(Media.type == "series", Media.title == title).limit(1)) is not None
        )
    title = (line.title or "").strip()
    if not title:
        return False
    row = db.scalar(select(Media).where(Media.type == "film", Media.title == title).limit(1))
    if row is None:
        return False
    if line.year is not None and row.year is not None and row.year != line.year:
        return False
    return True


def _fill_identify_counts(db: Session, secret_key: str, preview: ImportPreview) -> None:
    """Compte les fiches distinctes à créer sans identifiant TMDB (prévisualisation sans réseau)."""
    token = _token_reader()(db, secret_key)
    pending_keys: set[tuple] = set()
    no_token_keys: set[tuple] = set()
    for outcome in preview.to_create:
        line = outcome.line
        if line.tmdb_id is not None:
            continue
        if line.tvdb_id is not None or line.imdb_id:
            continue
        if _existing_media_for_identify(db, line):
            continue
        key = _identify_preview_media_key(line)
        if not token:
            no_token_keys.add(key)
        else:
            pending_keys.add(key)
    preview.identify_pending_count = len(pending_keys)
    preview.identify_no_token_count = len(no_token_keys)
    preview.identify_no_results_count = 0


class _ImportTx:
    """Commits intermédiaires pour ne pas tenir SQLite pendant TMDB."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self._last_commit = time.monotonic()
        self._since_commit = 0
        self.commits = 0
        db.info["import_commits"] = 0

    def _commit(self) -> None:
        commit_retry(self.db)
        self.commits += 1
        self.db.info["import_commits"] = self.commits
        self._last_commit = time.monotonic()
        self._since_commit = 0

    def release_before_network(self) -> None:
        self._commit()

    def note_processed(self) -> None:
        self._since_commit += 1
        elapsed = time.monotonic() - self._last_commit
        if self._since_commit >= IMPORT_COMMIT_BATCH or elapsed >= IMPORT_COMMIT_MAX_INTERVAL_S:
            self._commit()


def apply_import(
    db: Session,
    settings,
    secret_key: str,
    lines: list[HistoryLine],
    *,
    now: datetime | None = None,
    on_preview_progress: Callable[[int, int], None] | None = None,
) -> ImportResult:
    moment = now or datetime.now(timezone.utc)
    preview = preview_import(db, lines, on_progress=on_preview_progress)
    applied = already = 0
    # « Fiches créées » = nouvelles lignes Media (séries / films), comme le compteur
    # « fiches à créer » de la prévisualisation, et non les épisodes ajoutés.
    media_before = set(db.scalars(select(Media.id)).all())
    imported_media: set[int] = set()
    tmdb_cache: dict[tuple[str, int], Any] = {}
    find_cache: dict[tuple[str, str], tuple[int | None, str | None]] = {}
    search_cache: dict[tuple[str, str], list[Any]] = {}
    ensure_failed: list[LineOutcome] = []
    pending_outcomes: list[LineOutcome] = []
    for bucket in (preview.matched, preview.to_create):
        for outcome in bucket:
            if outcome.status in {"matched", "create"}:
                pending_outcomes.append(outcome)
    processed = 0
    import_tx = _ImportTx(db)
    from mediatracker.integrations.tmdb import TmdbClient, borrow_tmdb

    shared_token = _token_reader()(db, secret_key)
    shared_client = None
    shared_owned = False
    if shared_token:
        shared_client, shared_owned = borrow_tmdb(shared_token, TmdbClient)
    try:
        for outcome in pending_outcomes:
            ensured = _ensure_item(
                db,
                settings,
                secret_key,
                outcome.line,
                moment,
                tmdb_cache=tmdb_cache,
                find_cache=find_cache,
                search_cache=search_cache,
                import_tx=import_tx,
                tmdb_token=shared_token,
                tmdb_client=shared_client,
            )
            if ensured is None:
                ensure_failed.append(
                    LineOutcome(
                        line=outcome.line,
                        status="unmatched",
                        reason=_ensure_failed_reason(outcome.line),
                    )
                )
                continue
            item, _created_now = ensured
            imported_media.add(item.media_id)
            if _mark_watched(item, outcome.line, moment):
                applied += 1
            else:
                already += 1
            processed += 1
            import_tx.note_processed()
    finally:
        if shared_owned and shared_client is not None:
            shared_client.close()
    created = len(imported_media - media_before)
    if ensure_failed:
        failed_nos = {o.line.line_no for o in ensure_failed}
        preview.unmatched.extend(ensure_failed)
        preview.to_create = [o for o in preview.to_create if o.line.line_no not in failed_nos]
        preview.matched = [o for o in preview.matched if o.line.line_no not in failed_nos]
    candidates = _build_follow_candidates(db, imported_media)
    if imported_media:
        _store_follow_candidates(db, imported_media, moment)
    if imported_media and (applied > 0 or created > 0):
        record_event(
            db,
            "jellyfin_history.imported",
            payload={
                "lines": preview.line_count,
                "marked": applied,
                "created": created,
                "unmatched": len(preview.unmatched),
                "media_ids": sorted(imported_media),
            },
        )
    return ImportResult(
        preview=preview,
        applied=applied,
        created_media=created,
        already_ok=already,
        commits_persisted=import_tx.commits,
        follow_candidates=candidates,
    )


def _store_follow_candidates(db: Session, media_ids: set[int], now: datetime) -> None:
    payload = json.dumps(sorted(media_ids))
    row = db.get(Setting, FOLLOW_CANDIDATES_KEY)
    if row is None:
        db.add(Setting(key=FOLLOW_CANDIDATES_KEY, value=payload, is_secret=False, updated_at=now))
    else:
        row.value = payload
        row.updated_at = now


def _stored_candidate_ids(db: Session) -> set[int]:
    row = db.get(Setting, FOLLOW_CANDIDATES_KEY)
    if row is None or not row.value:
        return set()
    try:
        raw = json.loads(row.value)
    except json.JSONDecodeError:
        return set()
    if not isinstance(raw, list):
        return set()
    out: set[int] = set()
    for value in raw:
        if isinstance(value, int):
            out.add(value)
    return out


def _build_follow_candidates(db: Session, media_ids: set[int]) -> list[dict[str, Any]]:
    if not media_ids:
        return []
    rows = list(db.scalars(select(Media).where(Media.id.in_(media_ids)).order_by(Media.title)).all())
    return [_candidate_row(row) for row in rows]


def _candidate_row(row: Media) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title,
        "year": row.year,
        "type": row.type,
        "tmdb_id": row.tmdb_id,
        "followed": bool(row.followed),
    }


def list_follow_candidates(db: Session) -> list[dict[str, Any]]:
    ids = _stored_candidate_ids(db)
    if not ids:
        return []
    return _build_follow_candidates(db, ids)


def refollow_imported(db: Session, media_ids: list[int]) -> int:
    if not media_ids:
        return 0
    allowed = _stored_candidate_ids(db)
    stmt = select(Media).where(Media.id.in_(media_ids), Media.followed.is_(False))
    rows = list(db.scalars(stmt).all())
    if allowed:
        rows = [row for row in rows if row.id in allowed]
    from mediatracker.core.follow import follow

    for row in rows:
        follow(db, None, media=row, source="jellyfin_import_refollow", origin_label=FOLLOW_ORIGIN)
    return len(rows)


def list_unfollowed_imported(db: Session, *, limit: int = 200) -> list[Media]:
    """Backward-compatible helper: unfollowed rows from the last import batch."""
    ids = _stored_candidate_ids(db)
    if not ids:
        return []
    return list(
        db.scalars(
            select(Media)
            .where(Media.id.in_(ids), Media.followed.is_(False))
            .order_by(Media.title)
            .limit(limit)
        ).all()
    )


def _resolve_line(db: Session, line: HistoryLine) -> LineOutcome:
    media_type = "series" if line.row_type == "episode" else "film"
    watch = _as_watch(line)
    items = match_media_items(db, watch, None, None, None)
    if items:
        item = _pick_item(items, line)
        media = db.get(Media, item.media_id)
        return LineOutcome(
            line=line,
            status="matched",
            media_id=item.media_id,
            media_title=media.title if media else None,
            item_id=item.id,
        )
    conflict = _provider_id_conflict(db, line, media_type)
    if conflict:
        return LineOutcome(line=line, status="unmatched", reason=conflict)
    if _can_create(line):
        if line.row_type == "episode" and not (line.series_title or "").strip():
            if not (line.tvdb_id or line.imdb_id or line.tmdb_id):
                return LineOutcome(
                    line=line,
                    status="unmatched",
                    reason="Épisode sans titre de série ni identifiant fournisseur.",
                )
        return LineOutcome(
            line=line,
            status="create",
            create_media=True,
            media_title=line.series_title if line.row_type == "episode" else line.title,
        )
    reason = _unmatched_reason(db, line, watch)
    return LineOutcome(line=line, status="unmatched", reason=reason)


def _provider_id_conflict(db: Session, line: HistoryLine, media_type: str) -> str | None:
    """Conflit seulement si un id de la ligne correspond à une fiche et un autre id la contredit."""
    matched: dict[int, Media] = {}
    if line.tmdb_id is not None:
        row = db.scalar(select(Media).where(Media.tmdb_id == line.tmdb_id, Media.type == media_type))
        if row is not None:
            matched[row.id] = row
    if line.tvdb_id is not None:
        row = db.scalar(select(Media).where(Media.tvdb_id == line.tvdb_id, Media.type == media_type))
        if row is not None:
            matched[row.id] = row
    if line.imdb_id:
        row = db.scalar(
            select(Media).where(Media.imdb_id == line.imdb_id, Media.type == media_type)
        )
        if row is not None:
            matched[row.id] = row
    if not matched:
        return None
    if len(matched) > 1:
        return "Identifiants fournisseur incohérents (plusieurs fiches distinctes)."
    row = next(iter(matched.values()))
    if line.tmdb_id is not None and row.tmdb_id is not None and row.tmdb_id != line.tmdb_id:
        return "Conflit d'identifiant TMDB avec une fiche existante."
    if line.tvdb_id is not None and row.tvdb_id is not None and row.tvdb_id != line.tvdb_id:
        return "Conflit d'identifiant TVDB avec une fiche existante."
    if line.imdb_id and row.imdb_id and row.imdb_id.casefold() != line.imdb_id.casefold():
        return "Conflit d'identifiant IMDB avec une fiche existante."
    return None


def _can_create(line: HistoryLine) -> bool:
    if line.row_type == "movie" and line.title:
        return True
    if line.tmdb_id is not None or line.imdb_id or line.tvdb_id:
        if line.row_type == "movie":
            return True
        if line.season is not None and line.episode is not None:
            return True
    if line.row_type == "episode" and line.series_title and line.season is not None and line.episode is not None:
        return True
    return False


def _unmatched_reason(db: Session, line: HistoryLine, watch: JellyfinWatch) -> str:
    if line.jellyfin_item_id:
        return "Aucune fiche avec cet identifiant Jellyfin."
    if line.tmdb_id is not None:
        media = db.scalar(select(Media).where(Media.tmdb_id == line.tmdb_id).limit(1))
        if media is not None:
            return "Série ou film TMDB connu, mais épisode ou saison introuvable."
        return "Identifiant TMDB sans fiche correspondante et titre insuffisant."
    if line.row_type == "episode" and (line.season is None or line.episode is None):
        return "Épisode sans numéro de saison ou d'épisode."
    if line.row_type == "episode" and not (line.series_title or "").strip():
        if line.tvdb_id or line.imdb_id or line.tmdb_id:
            return (
                f"Série introuvable via TMDB ({_series_ids_attempted_label(line)}) : ligne non importable."
            )
        return "Épisode sans titre de série ni identifiant fournisseur."
    return "Titre et année insuffisants pour créer une fiche."


def _pick_item(items: list[MediaItem], line: HistoryLine) -> MediaItem:
    if line.row_type == "movie":
        films = [item for item in items if item.season is None and item.episode is None]
        return films[0] if films else items[0]
    if line.season is not None and line.episode is not None:
        for item in items:
            if item.season == line.season and item.episode == line.episode:
                return item
    return items[0]


def _ensure_item(
    db: Session,
    settings,
    secret_key: str,
    line: HistoryLine,
    now: datetime,
    *,
    tmdb_cache: dict[tuple[str, int], Any] | None = None,
    find_cache: dict[tuple[str, str], tuple[int | None, str | None]] | None = None,
    search_cache: dict[tuple[str, str], list[Any]] | None = None,
    import_tx: _ImportTx | None = None,
    tmdb_token: str = "",
    tmdb_client=None,
) -> tuple[MediaItem, bool] | None:
    watch = _as_watch(line)
    items = match_media_items(db, watch, None, None, None)
    if items:
        return _pick_item(items, line), False
    if line.row_type == "movie":
        return _create_film(
            db,
            settings,
            secret_key,
            line,
            now,
            tmdb_cache=tmdb_cache,
            find_cache=find_cache,
            search_cache=search_cache,
            import_tx=import_tx,
            tmdb_token=tmdb_token,
            tmdb_client=tmdb_client,
        )
    return _create_episode(
        db,
        settings,
        secret_key,
        line,
        now,
        tmdb_cache=tmdb_cache,
        find_cache=find_cache,
        search_cache=search_cache,
        import_tx=import_tx,
        tmdb_token=tmdb_token,
        tmdb_client=tmdb_client,
    )


def _create_film(
    db: Session,
    settings,
    secret_key: str,
    line: HistoryLine,
    now: datetime,
    *,
    tmdb_cache: dict[tuple[str, int], Any] | None = None,
    find_cache: dict[tuple[str, str], tuple[int | None, str | None]] | None = None,
    search_cache: dict[tuple[str, str], list[Any]] | None = None,
    import_tx: _ImportTx | None = None,
    tmdb_token: str = "",
    tmdb_client=None,
) -> tuple[MediaItem, bool] | None:
    before = set(db.scalars(select(Media.id)).all())
    media = _find_or_build_media(
        db,
        settings,
        secret_key,
        "film",
        line,
        now,
        series_line=False,
        tmdb_cache=tmdb_cache,
        find_cache=find_cache,
        search_cache=search_cache,
        import_tx=import_tx,
        tmdb_token=tmdb_token,
        tmdb_client=tmdb_client,
    )
    if media is None:
        return None
    created_media = media.id not in before
    item = db.scalar(select(MediaItem).where(MediaItem.media_id == media.id).limit(1))
    if item is None:
        item = MediaItem(media_id=media.id, title=media.title, state="missing", watched=False)
        db.add(item)
        db.flush()
    if line.jellyfin_item_id and not item.jellyfin_item_id:
        item.jellyfin_item_id = line.jellyfin_item_id
        # autoflush=False : la ligne suivante cherche par jellyfin_item_id en SQL.
        db.flush()
    return item, created_media


def _create_episode(
    db: Session,
    settings,
    secret_key: str,
    line: HistoryLine,
    now: datetime,
    *,
    tmdb_cache: dict[tuple[str, int], Any] | None = None,
    find_cache: dict[tuple[str, str], tuple[int | None, str | None]] | None = None,
    search_cache: dict[tuple[str, str], list[Any]] | None = None,
    import_tx: _ImportTx | None = None,
    tmdb_token: str = "",
    tmdb_client=None,
) -> tuple[MediaItem, bool] | None:
    media = _find_or_build_media(
        db,
        settings,
        secret_key,
        "series",
        line,
        now,
        series_line=True,
        tmdb_cache=tmdb_cache,
        find_cache=find_cache,
        search_cache=search_cache,
        import_tx=import_tx,
        tmdb_token=tmdb_token,
        tmdb_client=tmdb_client,
    )
    if media is None:
        return None
    created = False
    item = None
    if line.season is not None and line.episode is not None:
        item = db.scalar(
            select(MediaItem).where(
                MediaItem.media_id == media.id,
                MediaItem.season == line.season,
                MediaItem.episode == line.episode,
            )
        )
    if item is None:
        item = MediaItem(
            media_id=media.id,
            season=line.season,
            episode=line.episode,
            title=line.title,
            state="missing",
            watched=False,
        )
        db.add(item)
        db.flush()
        created = True
    if line.jellyfin_item_id and not item.jellyfin_item_id:
        item.jellyfin_item_id = line.jellyfin_item_id
        # autoflush=False : la ligne suivante cherche par jellyfin_item_id en SQL.
        db.flush()
    return item, created


def _series_imdb_for_find(imdb_id: str | None) -> str | None:
    if not imdb_id:
        return None
    text = imdb_id.strip()
    return text or None


def _series_ids_attempted_label(line: HistoryLine) -> str:
    parts: list[str] = []
    if line.tmdb_id is not None:
        parts.append(f"TMDB {line.tmdb_id}")
    if line.tvdb_id is not None:
        parts.append(f"TVDB {line.tvdb_id}")
    if line.imdb_id:
        parts.append(f"IMDB {line.imdb_id}")
    return ", ".join(parts) if parts else "aucun identifiant"


def _ensure_failed_reason(line: HistoryLine) -> str:
    if line.row_type == "episode" and not (line.series_title or "").strip():
        if line.tvdb_id or line.imdb_id or line.tmdb_id:
            return (
                f"Série introuvable via TMDB ({_series_ids_attempted_label(line)}) : ligne non importable."
            )
        return "Épisode sans titre de série ni identifiant fournisseur."
    if line.tmdb_id is not None:
        return "Identifiant TMDB sans fiche correspondante et titre insuffisant."
    return "Titre et année insuffisants pour créer une fiche."


def _resolve_series_via_tmdb(
    db: Session,
    settings,
    secret_key: str,
    line: HistoryLine,
    *,
    tmdb_cache: dict[tuple[str, int], Any] | None = None,
    find_cache: dict[tuple[str, str], tuple[int | None, str | None]] | None = None,
    import_tx: _ImportTx | None = None,
    tmdb_token: str = "",
    tmdb_client=None,
) -> tuple[str, int | None] | None:
    from mediatracker.integrations.tmdb import TmdbClient, TmdbError, borrow_tmdb

    if (line.series_title or "").strip():
        return line.series_title.strip(), line.tmdb_id
    if line.tmdb_id is not None:
        fetched = _maybe_fetch(
            db,
            settings,
            secret_key,
            line.tmdb_id,
            "series",
            tmdb_cache=tmdb_cache,
            import_tx=import_tx,
            tmdb_client=tmdb_client,
        )
        if fetched is not None:
            name = fetched.title or fetched.original_title
            if name:
                return name.strip(), line.tmdb_id
    if not tmdb_token:
        tmdb_token = _token_reader()(db, secret_key)
    if not tmdb_token:
        return None

    client = tmdb_client
    owned = False
    if client is None:
        client, owned = borrow_tmdb(tmdb_token, TmdbClient)

    def _find(source: str, external: str) -> tuple[int | None, str | None]:
        cache_key = (source, external)
        if find_cache is not None and cache_key in find_cache:
            return find_cache[cache_key]
        if import_tx is not None:
            import_tx.release_before_network()
        try:
            tmdb_id, kind = client.find_external(external, source)
        except TmdbError:
            tmdb_id, kind = None, None
        title = None
        if tmdb_id is not None and kind == "series":
            fetched = _maybe_fetch(
                db,
                settings,
                secret_key,
                tmdb_id,
                "series",
                tmdb_cache=tmdb_cache,
                import_tx=import_tx,
            )
            if fetched is not None:
                title = fetched.title or fetched.original_title
        result = (title, tmdb_id if kind == "series" else None)
        if find_cache is not None:
            find_cache[cache_key] = result
        return result

    try:
        tv_title, tv_tmdb = (None, None)
        im_title, im_tmdb = (None, None)
        if line.tvdb_id is not None:
            tv_title, tv_tmdb = _find("tvdb_id", str(line.tvdb_id))
        imdb = _series_imdb_for_find(line.imdb_id)
        if imdb:
            im_title, im_tmdb = _find("imdb_id", imdb)
        if tv_tmdb is not None and im_tmdb is not None and tv_tmdb != im_tmdb:
            return None
        if tv_title:
            return tv_title, tv_tmdb or line.tmdb_id
        if im_title:
            return im_title, im_tmdb or line.tmdb_id
        return None
    finally:
        if owned:
            client.close()


def _resolve_import_tmdb(
    db: Session,
    settings,
    secret_key: str,
    line: HistoryLine,
    media_type: str,
    *,
    series_line: bool,
    tmdb_cache: dict[tuple[str, int], Any] | None = None,
    find_cache: dict[tuple[str, str], tuple[int | None, str | None]] | None = None,
    search_cache: dict[tuple[str, str], list[Any]] | None = None,
    import_tx: _ImportTx | None = None,
    tmdb_token: str = "",
    tmdb_client=None,
) -> ImportTmdbResolution:
    from mediatracker.integrations.tmdb import TmdbClient, TmdbError, borrow_tmdb
    from mediatracker.library.parse import ParsedName

    identify_fn = _identify
    if identify_fn is None:
        raise RuntimeError("Jellyfin import TMDB wiring missing (wire_jellyfin_integrations)")

    if line.tmdb_id is not None:
        return ImportTmdbResolution(tmdb_id=line.tmdb_id)
    if not tmdb_token:
        tmdb_token = _token_reader()(db, secret_key)
    if not tmdb_token:
        return ImportTmdbResolution(identify_status=TMDB_IDENTIFY_NO_TOKEN)

    client = tmdb_client
    owned = False
    if client is None:
        client, owned = borrow_tmdb(tmdb_token, TmdbClient)

    def _find(source: str, external: str) -> tuple[int | None, str | None]:
        cache_key = (source, external)
        if find_cache is not None and cache_key in find_cache:
            return find_cache[cache_key]
        if import_tx is not None:
            import_tx.release_before_network()
        try:
            tmdb_id, kind = client.find_external(external, source)
        except TmdbError:
            tmdb_id, kind = None, None
        result = (tmdb_id, kind)
        if find_cache is not None:
            find_cache[cache_key] = result
        return result

    try:
        resolved_id: int | None = None
        if line.tvdb_id is not None:
            found, kind = _find("tvdb_id", str(line.tvdb_id))
            if found is not None and kind == media_type:
                resolved_id = found
        imdb = _series_imdb_for_find(line.imdb_id)
        if resolved_id is None and imdb:
            found, kind = _find("imdb_id", imdb)
            if found is not None and kind == media_type:
                resolved_id = found
        if resolved_id is not None:
            fetched = _maybe_fetch(
                db,
                settings,
                secret_key,
                resolved_id,
                media_type,
                tmdb_cache=tmdb_cache,
                import_tx=import_tx,
                tmdb_client=tmdb_client,
            )
            return ImportTmdbResolution(tmdb_id=resolved_id, fetched=fetched)

        title = ((line.series_title if series_line else line.title) or "").strip()
        if not title:
            return ImportTmdbResolution(identify_status=TMDB_IDENTIFY_NO_RESULTS)
        search_year = line.series_year if series_line else line.year
        parsed = ParsedName(
            title=title,
            year=search_year,
            season=None,
            episode=None,
            tmdb_id=None,
            quality=None,
            language=None,
            media_type=media_type,
        )
        from mediatracker.library.parse import normalize_title

        cache_key = (media_type, normalize_title(title))
        hits: list[Any]
        if search_cache is not None and cache_key in search_cache:
            hits = search_cache[cache_key]
        else:
            if import_tx is not None:
                import_tx.release_before_network()
            try:
                hits = client.search(title, media_type)
            except TmdbError:
                hits = []
            if search_cache is not None:
                search_cache[cache_key] = hits
        if not hits:
            return ImportTmdbResolution(identify_status=TMDB_IDENTIFY_NO_RESULTS)
        outcome = identify_fn(hits, parsed, tmdb=None)
        if outcome.hit is not None:
            fetched = _maybe_fetch(
                db,
                settings,
                secret_key,
                outcome.hit.tmdb_id,
                media_type,
                tmdb_cache=tmdb_cache,
                import_tx=import_tx,
                tmdb_client=tmdb_client,
            )
            return ImportTmdbResolution(tmdb_id=outcome.hit.tmdb_id, fetched=fetched)
        return ImportTmdbResolution(candidates=list(outcome.candidates), identify_status=TMDB_IDENTIFY_PENDING)
    finally:
        if owned:
            client.close()


def _find_or_build_media(
    db: Session,
    settings,
    secret_key: str,
    media_type: str,
    line: HistoryLine,
    now: datetime,
    *,
    series_line: bool,
    tmdb_cache: dict[tuple[str, int], Any] | None = None,
    find_cache: dict[tuple[str, str], tuple[int | None, str | None]] | None = None,
    search_cache: dict[tuple[str, str], list[Any]] | None = None,
    import_tx: _ImportTx | None = None,
    tmdb_token: str = "",
    tmdb_client=None,
) -> Media | None:
    existing = _lookup_media(db, line, media_type)
    if existing is not None:
        if series_line and (line.series_title or "").strip() and not existing.title.strip():
            existing.title = line.series_title.strip()
        return existing
    fetched = None
    tmdb_id = line.tmdb_id
    title = line.series_title if series_line else line.title
    year = line.series_year if series_line else line.year
    identify_candidates: list[dict[str, Any]] | None = None
    identify_status: str | None = None
    if series_line and not (title or "").strip():
        resolved = _resolve_series_via_tmdb(
            db,
            settings,
            secret_key,
            line,
            tmdb_cache=tmdb_cache,
            find_cache=find_cache,
            import_tx=import_tx,
            tmdb_token=tmdb_token,
            tmdb_client=tmdb_client,
        )
        if resolved is None:
            return None
        title, resolved_tmdb = resolved
        if resolved_tmdb is not None:
            tmdb_id = resolved_tmdb
    if tmdb_id is None:
        resolved = _resolve_import_tmdb(
            db,
            settings,
            secret_key,
            line,
            media_type,
            series_line=series_line,
            tmdb_cache=tmdb_cache,
            find_cache=find_cache,
            search_cache=search_cache,
            import_tx=import_tx,
            tmdb_token=tmdb_token,
            tmdb_client=tmdb_client,
        )
        tmdb_id = resolved.tmdb_id
        if fetched is None:
            fetched = resolved.fetched
        identify_candidates = resolved.candidates
        identify_status = resolved.identify_status
    if tmdb_id is not None and fetched is None:
        fetched = _maybe_fetch(
            db,
            settings,
            secret_key,
            tmdb_id,
            media_type,
            tmdb_cache=tmdb_cache,
            import_tx=import_tx,
            tmdb_client=tmdb_client,
        )
    if fetched is not None:
        title = fetched.title or title
        year = fetched.year or year
    if series_line and not (title or "").strip():
        return None
    if fetched is not None and tmdb_id is not None:
        create_fn = _create_from_tmdb
        if create_fn is None:
            raise RuntimeError("Jellyfin import TMDB wiring missing (wire_jellyfin_integrations)")

        media, _episodes = create_fn(
            db,
            media_type=media_type,
            tmdb_id=tmdb_id,
            fetched=fetched,
            now=now,
            title=title or (line.title if not series_line else ""),
            year=year,
            imdb_id=line.imdb_id,
            followed=False,
            follow_origin=FOLLOW_ORIGIN,
            tvdb_id=line.tvdb_id,
        )
        if media_type == "series":
            db.flush()
    else:
        media = Media(
            type=media_type,
            title=title or (line.title if not series_line else ""),
            year=year,
            tmdb_id=tmdb_id,
            imdb_id=line.imdb_id,
            tvdb_id=line.tvdb_id,
            followed=False,
            follow_origin=FOLLOW_ORIGIN,
            created_at=now,
        )
        if not media.title:
            return None
        db.add(media)
        db.flush()
    if identify_candidates or identify_status:
        media.last_repair_summary = IDENTIFY_PENDING_SUMMARY
        media.tmdb_identify_status = identify_status or TMDB_IDENTIFY_PENDING
        media.tmdb_identify_candidates = identify_candidates
    return media


def _lookup_media(db: Session, line: HistoryLine, media_type: str) -> Media | None:
    has_provider_ids = (
        line.tmdb_id is not None or bool(line.imdb_id) or line.tvdb_id is not None
    )
    if line.tmdb_id is not None:
        row = db.scalar(select(Media).where(Media.tmdb_id == line.tmdb_id, Media.type == media_type))
        if row is not None:
            if has_provider_ids and not _identity_matches(
                row, line.tmdb_id, line.tvdb_id, line.imdb_id, [], line.year
            ):
                return None
            return row
    if line.tvdb_id is not None:
        row = db.scalar(select(Media).where(Media.tvdb_id == line.tvdb_id, Media.type == media_type))
        if row is not None:
            if has_provider_ids and not _identity_matches(
                row, line.tmdb_id, line.tvdb_id, line.imdb_id, [], line.year
            ):
                return None
            return row
    if line.imdb_id:
        row = db.scalar(select(Media).where(Media.imdb_id == line.imdb_id, Media.type == media_type))
        if row is not None:
            if line.tvdb_id is not None and row.tvdb_id is not None and row.tvdb_id != line.tvdb_id:
                return None
            if has_provider_ids and not _identity_matches(
                row, line.tmdb_id, line.tvdb_id, line.imdb_id, [], line.year
            ):
                return None
            return row
    if has_provider_ids:
        return None
    title = line.series_title if media_type == "series" else line.title
    if title:
        stmt = select(Media).where(Media.type == media_type, Media.title == title)
        if line.year is not None:
            stmt = stmt.where(Media.year == line.year)
        row = db.scalar(stmt.limit(1))
        if row is not None and _identity_matches(
            row,
            line.tmdb_id,
            line.tvdb_id,
            line.imdb_id,
            [title],
            line.year,
        ):
            return row
    return None


def _maybe_fetch(
    db: Session,
    settings,
    secret_key: str,
    tmdb_id: int,
    media_type: str,
    *,
    tmdb_cache: dict[tuple[str, int], Any] | None = None,
    import_tx: _ImportTx | None = None,
    tmdb_client=None,
):
    from mediatracker.integrations.tmdb import TmdbError

    fetch_fn = _fetch_title
    if fetch_fn is None:
        raise RuntimeError("Jellyfin import TMDB wiring missing (wire_jellyfin_integrations)")

    key = (media_type, tmdb_id)
    if tmdb_cache is not None and key in tmdb_cache:
        return tmdb_cache[key]
    if import_tx is not None:
        import_tx.release_before_network()
    if tmdb_client is not None:
        try:
            result = tmdb_client.fetch(tmdb_id, media_type)
        except TmdbError:
            result = None
    else:
        result = fetch_fn(db, settings, tmdb_id, media_type)
    if tmdb_cache is not None:
        tmdb_cache[key] = result
    return result


def _mark_watched(item: MediaItem, line: HistoryLine, now: datetime) -> bool:
    if item.watched and line.watched_at is None:
        return False
    target = _aware(line.watched_at or now)
    current = _aware(item.watched_at) if item.watched_at is not None else None
    if item.watched and current is not None and current >= target:
        return False
    if item.watched and current is not None and line.watched_at is not None:
        if abs((current - target).total_seconds()) < 1:
            return False
    item.watched = True
    item.watched_at = target
    item.watch_source = "import"
    item.state = "watched"
    if line.jellyfin_item_id:
        if not item.jellyfin_item_id:
            item.jellyfin_item_id = line.jellyfin_item_id
        elif item.jellyfin_item_id != line.jellyfin_item_id:
            logger.warning(
                "Import Jellyfin : conflit jellyfin_item_id sur l'épisode/film %s (%s vs %s).",
                item.id,
                item.jellyfin_item_id,
                line.jellyfin_item_id,
            )
    return True


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _as_watch(line: HistoryLine) -> JellyfinWatch:
    item_type = "Movie" if line.row_type == "movie" else "Episode"
    series_tmdb = line.tmdb_id if line.row_type == "episode" else None
    film_tmdb = line.tmdb_id if line.row_type == "movie" else None
    return JellyfinWatch(
        item_id=line.jellyfin_item_id or "",
        item_type=item_type,
        tmdb_id=film_tmdb,
        series_tmdb_id=series_tmdb,
        episode_tmdb_id=line.tmdb_id if line.row_type == "episode" else None,
        season=line.season,
        episode=line.episode,
        path=None,
        played=True,
        tvdb_id=line.tvdb_id,
        imdb_id=line.imdb_id,
        series_name=line.series_title,
        series_year=line.year if line.row_type == "episode" else None,
        last_played_at=line.watched_at,
    )


def _opt_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def _opt_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _opt_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _norm_title(value: str | None) -> str:
    return " ".join((value or "").split()).casefold()


def count_media_to_create(outcomes: list[LineOutcome]) -> tuple[int, int]:
    """Nombre de fiches distinctes (séries, films) derrière les lignes « à créer ».

    Regroupe comme apply_import (via _lookup_media) : deux lignes qui partagent un
    identifiant fournisseur (TMDB, TVDB, IMDB) du même type visent la même fiche ;
    une ligne sans identifiant est regroupée par titre de série (séries) ou par
    titre + année (films), et rejoint le groupe à identifiants de même titre s'il
    est unique.
    """
    parent: dict[Any, Any] = {}

    def find(key: Any) -> Any:
        parent.setdefault(key, key)
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(a: Any, b: Any) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    title_only: list[tuple[str, Any, Any]] = []
    titles_of_id_groups: list[tuple[str, Any, Any]] = []
    for out in outcomes:
        line = out.line
        kind = "series" if line.row_type == "episode" else "film"
        ids: list[Any] = []
        if line.tmdb_id is not None:
            ids.append((kind, "tmdb", line.tmdb_id))
        if line.imdb_id:
            ids.append((kind, "imdb", line.imdb_id.strip().casefold()))
        if line.tvdb_id is not None:
            ids.append((kind, "tvdb", line.tvdb_id))
        if kind == "series":
            title_key = (kind, "title", _norm_title(line.series_title)) if _norm_title(line.series_title) else None
        else:
            title_key = (kind, "title", _norm_title(line.title), line.year) if _norm_title(line.title) else None
        if ids:
            find(ids[0])
            for other in ids[1:]:
                union(ids[0], other)
            if title_key is not None:
                titles_of_id_groups.append((kind, title_key, ids[0]))
        elif title_key is not None:
            title_only.append((kind, title_key, title_key))
        else:
            find((kind, "line", line.line_no))
    id_groups_by_title: dict[Any, set[Any]] = {}
    for _kind, title_key, anchor in titles_of_id_groups:
        id_groups_by_title.setdefault(title_key, set()).add(find(anchor))
    for _kind, title_key, _ in title_only:
        find(title_key)
        groups = id_groups_by_title.get(title_key, set())
        if len(groups) == 1:
            union(next(iter(groups)), title_key)
    roots = {find(key) for key in list(parent)}
    series = sum(1 for root in roots if root[0] == "series")
    films = sum(1 for root in roots if root[0] == "film")
    return series, films


def preview_to_dict(preview: ImportPreview) -> dict[str, Any]:
    def _line(out: LineOutcome) -> dict[str, Any]:
        return {
            "line": out.line.line_no,
            "type": out.line.row_type,
            "title": out.line.title,
            "series_title": out.line.series_title,
            "season": out.line.season,
            "episode": out.line.episode,
            "media_id": out.media_id,
            "media_title": out.media_title,
            "reason": out.reason,
        }

    create_series, create_films = count_media_to_create(preview.to_create)
    return {
        "line_count": preview.line_count,
        "matched_count": len(preview.matched),
        "create_count": len(preview.to_create),
        "create_media_count": create_series + create_films,
        "create_series_count": create_series,
        "create_film_count": create_films,
        "unmatched_count": len(preview.unmatched),
        "skipped_count": len(preview.skipped),
        "identify_pending_count": preview.identify_pending_count,
        "identify_no_token_count": preview.identify_no_token_count,
        "identify_no_results_count": preview.identify_no_results_count,
        "matched": [_line(row) for row in preview.matched[:100]],
        "to_create": [_line(row) for row in preview.to_create[:100]],
        "unmatched": [_line(row) for row in preview.unmatched[:100]],
        "skipped": [_line(row) for row in preview.skipped[:20]],
    }
