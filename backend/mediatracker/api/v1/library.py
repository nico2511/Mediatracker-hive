"""Manual library scan and the unmatched-file review list."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, load_only

from mediatracker.api.deps import get_db, require_scope
from mediatracker.api.errors import Problem
from mediatracker.core.library_plan import (
    LibraryPlanError,
    TrashedFile,
    identify_folder,
    list_folders,
    preview_empty_folders,
    remove_empty_folders,
    rename_folder,
    undo_rename,
)
from mediatracker.core.naming import sanitize
from mediatracker.core.tmdb import token as tmdb_token
from mediatracker.db.models import LibraryFile, Media, Setting, TrashEntry
from mediatracker.integrations.jellyfin_sync import jellyfin_settings, schedule_library_refresh
from mediatracker.integrations.tmdb import TmdbClient, TmdbError, borrow_tmdb
from mediatracker.library.scanner import library_roots, match_file, scan_library

router = APIRouter(prefix="/library", tags=["library"])


def _tmdb_for(token: str | None):
    if not token:
        return None, False
    return borrow_tmdb(token, TmdbClient)


class ScanOut(BaseModel):
    seen: int
    matched: int
    review: int
    unmatched: int
    missing: int
    followed: int


class VerifyAllProgressOut(BaseModel):
    running: bool = False
    started_at: datetime | None = None
    processed: int = 0
    total: int = 0
    batch: int = 0
    stop_reason: str | None = None


class VerifyAllProblemOut(BaseModel):
    media_id: int
    title: str
    summary: str
    files_missing: int = 0


class VerifyAllSummaryOut(BaseModel):
    at: datetime
    processed: int
    total: int
    files_found: int
    files_missing: int
    links_available: int
    step_errors: int = 0
    problems: list[VerifyAllProblemOut] = Field(default_factory=list)
    stopped_early: bool = False
    stop_reason: str | None = None
    detail: str


class VerifyAllStateOut(BaseModel):
    progress: VerifyAllProgressOut
    last_summary: VerifyAllSummaryOut | None = None


class CandidateOut(BaseModel):
    tmdb_id: int
    title: str
    year: int | None = None
    media_type: str
    original_title: str | None = None
    poster_url: str | None = None
    overview: str | None = None
    season_count: int | None = None
    episode_count: int | None = None
    runtime: int | None = None


class LibraryFileOut(BaseModel):
    id: int
    path: str
    size: int
    quality: str | None
    language: str | None
    parsed_title: str
    parsed_year: int | None
    season: int | None
    episode: int | None
    tmdb_id: int | None
    media_type: str
    media_id: int | None
    media_item_id: int | None
    status: str
    confidence: str
    note: str | None
    original_name: str | None = None
    candidates: list[CandidateOut]


class LibraryFilePage(BaseModel):
    items: list[LibraryFileOut]


class MatchIn(BaseModel):
    tmdb_id: int = Field(ge=1)
    type: str | None = Field(default=None, pattern="^(film|series)$")


def _out(row: LibraryFile) -> LibraryFileOut:
    candidates = []
    for item in row.candidates or []:
        if not isinstance(item, dict) or not isinstance(item.get("tmdb_id"), int):
            continue
        candidates.append(
            CandidateOut(
                tmdb_id=item["tmdb_id"],
                title=str(item.get("title") or ""),
                year=item.get("year") if isinstance(item.get("year"), int) else None,
                media_type=str(item.get("media_type") or row.media_type),
                original_title=item.get("original_title") if isinstance(item.get("original_title"), str) else None,
                poster_url=item.get("poster_url") if isinstance(item.get("poster_url"), str) else None,
                overview=item.get("overview") if isinstance(item.get("overview"), str) else None,
                season_count=item.get("season_count") if isinstance(item.get("season_count"), int) else None,
                episode_count=item.get("episode_count") if isinstance(item.get("episode_count"), int) else None,
                runtime=item.get("runtime") if isinstance(item.get("runtime"), int) else None,
            )
        )
    return LibraryFileOut(
        id=row.id,
        path=row.path,
        size=row.size,
        quality=row.quality,
        language=row.language,
        parsed_title=row.parsed_title,
        parsed_year=row.parsed_year,
        season=row.season,
        episode=row.episode,
        tmdb_id=row.tmdb_id,
        media_type=row.media_type,
        media_id=row.media_id,
        media_item_id=row.media_item_id,
        status=row.status,
        confidence=row.confidence,
        note=row.note,
        original_name=row.original_name,
        candidates=candidates,
    )


@router.post("/scan", response_model=ScanOut)
def run_scan(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> ScanOut:
    del _principal
    settings = request.app.state.settings
    movies, series = library_roots(db, settings)
    token = tmdb_token.read_tmdb_token(db, settings.secret_key)
    client, owned = _tmdb_for(token)
    try:
        report = scan_library(
            db, movies=movies, series=series, tmdb=client, skip=_scan_skip(db, settings), settings=settings
        )
    finally:
        if owned and client is not None:
            client.close()
    db.commit()
    return ScanOut(
        seen=report.seen,
        matched=report.matched,
        review=report.review,
        unmatched=report.unmatched,
        missing=report.missing,
        followed=report.followed,
    )


def _verify_all_out(raw: dict) -> VerifyAllStateOut:
    progress = raw.get("progress") or {}
    last = raw.get("last_summary")
    started = progress.get("started_at")
    parsed_start = None
    if started:
        try:
            parsed_start = datetime.fromisoformat(str(started))
        except ValueError:
            parsed_start = None
    summary_out = None
    if isinstance(last, dict) and last.get("at"):
        try:
            at = datetime.fromisoformat(str(last["at"]))
        except ValueError:
            at = None
        if at is not None:
            problems = [
                VerifyAllProblemOut(
                    media_id=int(row["media_id"]),
                    title=str(row.get("title") or ""),
                    summary=str(row.get("summary") or ""),
                    files_missing=int(row.get("files_missing") or 0),
                )
                for row in last.get("problems") or []
                if isinstance(row, dict) and row.get("media_id") is not None
            ]
            summary_out = VerifyAllSummaryOut(
                at=at,
                processed=int(last.get("processed") or 0),
                total=int(last.get("total") or 0),
                files_found=int(last.get("files_found") or 0),
                files_missing=int(last.get("files_missing") or 0),
                links_available=int(last.get("links_available") or 0),
                step_errors=int(last.get("step_errors") or 0),
                problems=problems,
                stopped_early=bool(last.get("stopped_early")),
                stop_reason=last.get("stop_reason") if isinstance(last.get("stop_reason"), str) else None,
                detail=str(last.get("detail") or ""),
            )
    return VerifyAllStateOut(
        progress=VerifyAllProgressOut(
            running=bool(progress.get("running")),
            started_at=parsed_start,
            processed=int(progress.get("processed") or 0),
            total=int(progress.get("total") or 0),
            batch=int(progress.get("batch") or 0),
            stop_reason=progress.get("stop_reason") if isinstance(progress.get("stop_reason"), str) else None,
        ),
        last_summary=summary_out,
    )


@router.get("/verify-all", response_model=VerifyAllStateOut)
def verify_all_state(
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> VerifyAllStateOut:
    """Progress of the library verify-all job and the last finished summary."""
    del _principal
    from mediatracker.core.verify_all import snapshot

    return _verify_all_out(snapshot(db))


@router.post("/verify-all", response_model=VerifyAllStateOut)
def verify_all_start(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> VerifyAllStateOut:
    """Start verifying every followed fiche in the background (batched repair pipeline)."""
    del _principal
    from mediatracker.core.verify_all import snapshot, try_start

    state = snapshot(db)
    if state["progress"].get("running"):
        raise Problem(
            409,
            "Conflit",
            "Une vérification de la bibliothèque est déjà en cours.",
            extra=_verify_all_out(state).model_dump(mode="json"),
        )
    try_start(request.app.state.session_factory, request.app.state.settings)
    return _verify_all_out(snapshot(db))


@router.get("/files", response_model=LibraryFilePage)
def list_files(
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
    status: str | None = Query(default=None, pattern="^(matched|review|unmatched|missing)$"),
) -> LibraryFilePage:
    del _principal
    stmt = select(LibraryFile)
    if status is None:
        stmt = stmt.where(LibraryFile.status.in_(("review", "unmatched")))
    else:
        stmt = stmt.where(LibraryFile.status == status)
    rows = list(db.scalars(stmt.order_by(LibraryFile.path)).all())
    return LibraryFilePage(items=[_out(row) for row in rows])


@router.post("/files/{file_id}/match", response_model=LibraryFileOut)
def match_library_file(
    file_id: int,
    body: MatchIn,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> LibraryFileOut:
    del _principal
    row = db.get(LibraryFile, file_id)
    if row is None:
        raise Problem(404, "Introuvable", "Fichier introuvable.")
    media_type = body.type or row.media_type
    token = tmdb_token.read_tmdb_token(db, request.app.state.settings.secret_key)
    client, owned = _tmdb_for(token)
    try:
        match_file(
            db,
            row,
            tmdb_id=body.tmdb_id,
            media_type=media_type,
            now=datetime.now(timezone.utc),
            tmdb=client,
        )
    except ValueError as exc:
        code = str(exc)
        if code == "MISSING":
            raise Problem(422, "Requête invalide", "Ce fichier n'est plus sur le disque.") from exc
        if code == "EPISODE":
            raise Problem(422, "Requête invalide", "Saison ou épisode illisible : renommez le fichier.") from exc
        raise Problem(422, "Requête invalide", "Type de média inconnu.") from exc
    finally:
        if owned and client is not None:
            client.close()
    db.commit()
    db.refresh(row)
    return _out(row)


class FolderFileOut(BaseModel):
    path: str
    name: str
    season: int | None
    episode: int | None


class SuggestionOut(BaseModel):
    tmdb_id: int
    title: str
    year: int | None = None
    media_type: str
    original_title: str | None = None
    poster_url: str | None = None
    overview: str | None = None
    season_count: int | None = None
    episode_count: int | None = None
    runtime: int | None = None


class FolderOut(BaseModel):
    path: str
    name: str
    media_type: str
    title: str
    year: int | None
    tmdb_id: int | None
    confidence: str
    source: str
    files: list[FolderFileOut]
    identified: bool = False
    renamed: bool = False
    followed: bool = False
    on_hold: bool = False
    follow_origin: str | None = None
    media_id: int | None = None


class FolderPage(BaseModel):
    items: list[FolderOut]


class IdentifyIn(BaseModel):
    path: str = Field(min_length=1, max_length=500)


class IdentifyOut(BaseModel):
    tmdb_id: int | None
    confidence: str
    source: str
    title: str
    year: int | None
    suggestions: list[SuggestionOut]


class RenameIn(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    tmdb_id: int | None = Field(default=None, ge=1)
    title: str | None = Field(default=None, max_length=300)
    year: int | None = Field(default=None, ge=1880, le=2100)


class RenameOut(BaseModel):
    id: int | None = None
    moves: int
    already: bool = False


class UndoIn(BaseModel):
    id: int = Field(ge=1)


@dataclass
class _FolderMediaIndex:
    by_tmdb: dict[tuple[int, str], Media]
    by_type: dict[str, list[Media]]

    def resolve(self, row) -> Media | None:
        if row.tmdb_id is not None:
            found = self.by_tmdb.get((row.tmdb_id, row.media_type))
            if found is not None:
                return found
        folded = row.title.casefold()
        for media in self.by_type.get(row.media_type, ()):
            if media.title.casefold() == folded and (row.year is None or media.year in {None, row.year}):
                return media
        return None


def _folder_media_index(db: Session, rows) -> _FolderMediaIndex:
    types = {row.media_type for row in rows}
    if not types:
        return _FolderMediaIndex({}, {})
    media_rows = list(
        db.scalars(
            select(Media)
            .where(Media.type.in_(types))
            .options(
                load_only(
                    Media.id,
                    Media.type,
                    Media.title,
                    Media.year,
                    Media.tmdb_id,
                    Media.followed,
                    Media.on_hold,
                    Media.follow_origin,
                )
            )
        ).all()
    )
    by_tmdb: dict[tuple[int, str], Media] = {}
    by_type: dict[str, list[Media]] = {kind: [] for kind in types}
    for media in media_rows:
        by_type.setdefault(media.type, []).append(media)
        if media.tmdb_id is not None:
            key = (media.tmdb_id, media.type)
            if key not in by_tmdb:
                by_tmdb[key] = media
    return _FolderMediaIndex(by_tmdb, by_type)


def _folder_out(row, db: Session | None = None, *, media_index: _FolderMediaIndex | None = None) -> FolderOut:
    if media_index is not None:
        media = media_index.resolve(row)
    elif db is not None:
        media = _media_for_folder(db, row)
    else:
        media = None
    identified = row.tmdb_id is not None or (media is not None and media.tmdb_id is not None)
    return FolderOut(
        path=row.path,
        name=row.name,
        media_type=row.media_type,
        title=row.title,
        year=row.year,
        tmdb_id=row.tmdb_id or (media.tmdb_id if media is not None else None),
        confidence=row.confidence,
        source=row.source,
        files=[
            FolderFileOut(path=item.path, name=item.name, season=item.season, episode=item.episode)
            for item in row.files
        ],
        identified=identified,
        renamed=_renamed(row),
        followed=bool(media is not None and media.followed),
        on_hold=bool(media is not None and media.on_hold),
        follow_origin=media.follow_origin if media is not None else None,
        media_id=media.id if media is not None else None,
    )


def _media_for_folder(db: Session, row) -> Media | None:
    return _folder_media_index(db, [row]).resolve(row)


def _renamed(row) -> bool:
    import unicodedata

    stem = sanitize(row.title)
    expected = f"{stem} ({row.year})" if row.year else stem
    return unicodedata.normalize("NFC", row.name) == unicodedata.normalize("NFC", expected)


def _locate(db: Session, settings, path: str):
    from pathlib import Path

    movies, series = library_roots(db, settings)
    target = Path(path)
    if not target.is_dir():
        raise Problem(404, "Introuvable", "Dossier introuvable.")
    resolved = target.resolve()
    for root, kind in ((movies, "film"), (series, "series")):
        try:
            resolved.relative_to(root.resolve())
        except ValueError:
            continue
        if resolved == root.resolve():
            break
        return resolved, root, kind
    raise Problem(422, "Requête invalide", "Le dossier est en dehors des bibliothèques Films et Séries.")


class SeasonOut(BaseModel):
    season: int
    episodes: int
    label: str


class ReviewFolderOut(BaseModel):
    path: str
    name: str
    title: str
    year: int | None = None
    media_type: str
    tmdb_id: int | None = None
    file_count: int
    canonical: bool
    seasons: list[SeasonOut]


class ReviewMoveOut(BaseModel):
    src: str
    dest: str
    name: str
    blocked: str | None = None


class ReviewFileOut(BaseModel):
    path: str
    name: str
    kind: str
    reason: str
    season: int | None = None
    episode: int | None = None
    target_path: str
    target_name: str
    blocked: str | None = None
    original_name: str | None = None
    action: str = ""
    size: int = 0
    other_size: int | None = None
    warning: str | None = None


class ReviewSeriesOut(BaseModel):
    key: str
    title: str
    year: int | None = None
    tmdb_id: int | None = None
    poster_url: str | None = None
    media_type: str
    media_id: int | None = None
    followed: bool
    problem: str | None = None
    duplicate: bool
    merge_hint: str | None = None
    folders: list[ReviewFolderOut]
    merges: list[ReviewMoveOut]
    files: list[ReviewFileOut]
    empty_after: list[str] = Field(default_factory=list)


class SuspectFileOut(BaseModel):
    path: str
    name: str
    size: int
    media_id: int | None = None
    message: str


class ReviewOut(BaseModel):
    series: list[ReviewSeriesOut]
    empty: list[EmptyFolderOut]
    mismatch: int
    duplicate_folders: int
    unmatched: int
    empty_count: int
    suspect_files: list[SuspectFileOut] = Field(default_factory=list)


class ApplyFilesIn(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=500)
    validated: bool = False


class ApplyMergeIn(BaseModel):
    key: str = Field(min_length=1, max_length=500)
    validated: bool = False


class ApplyOut(BaseModel):
    applied: bool
    id: int | None = None
    moves: int
    skipped: int = 0


class RegroupMoveOut(BaseModel):
    src: str
    dest: str
    name: str
    original_name: str
    blocked: str | None = None
    action: str = "move"
    size: int = 0
    other_size: int | None = None
    warning: str | None = None


class RegroupGroupOut(BaseModel):
    media_id: int | None = None
    title: str
    year: int | None
    tmdb_id: int | None
    media_type: str
    canonical: str
    match: str
    folders: list[str]
    moves: list[RegroupMoveOut]
    empty_after: list[str]
    loose: bool = False


class RegroupOut(BaseModel):
    groups: list[RegroupGroupOut]
    unmatched: list[str]
    ambiguous: list[str]
    empty: list[str]
    loose: list[RegroupGroupOut] = Field(default_factory=list)
    rename_id: int | None = None
    applied: bool = False


class RegroupIn(BaseModel):
    validated: bool = False
    media_ids: list[int] = Field(default_factory=list, max_length=200)
    include_loose: bool = False


class IncompleteFileOut(BaseModel):
    path: str
    name: str
    size: int
    reason: str
    written_bytes: int = 0
    reserved_bytes: int = 0
    job_id: int | None = None
    job_state: str = ""
    orphan: bool = True


class IncompleteOut(BaseModel):
    files: list[IncompleteFileOut]
    moved: int = 0
    failed: int = 0
    errors: list[str] = Field(default_factory=list)
    trash: str = ""
    auto_enabled: bool = False
    last_run_at: datetime | None = None
    auto_message: str = ""
    folder_written_bytes: int = 0
    folder_reserved_bytes: int = 0
    total_written_bytes: int = 0
    trash_ids: list[int] = Field(default_factory=list)


class IncompleteIn(BaseModel):
    validated: bool = False
    paths: list[str] = Field(default_factory=list, max_length=500)


def _review_out(report) -> ReviewOut:
    return ReviewOut(
        series=[
            ReviewSeriesOut(
                key=row.key,
                title=row.title,
                year=row.year,
                tmdb_id=row.tmdb_id,
                poster_url=row.poster_url,
                media_type=row.media_type,
                media_id=row.media_id,
                followed=row.followed,
                problem=row.problem,
                duplicate=row.duplicate,
                merge_hint=row.merge_hint,
                folders=[
                    ReviewFolderOut(
                        path=folder.path,
                        name=folder.name,
                        title=folder.title,
                        year=folder.year,
                        media_type=folder.media_type,
                        tmdb_id=folder.tmdb_id,
                        file_count=folder.file_count,
                        canonical=folder.canonical,
                        seasons=[
                            SeasonOut(season=item.season, episodes=item.episodes, label=item.label)
                            for item in folder.seasons
                        ],
                    )
                    for folder in row.folders
                ],
                merges=[
                    ReviewMoveOut(src=item.src, dest=item.dest, name=item.name, blocked=item.blocked)
                    for item in row.merges
                ],
                files=[
                    ReviewFileOut(
                        path=item.path,
                        name=item.name,
                        kind=item.kind,
                        reason=item.reason,
                        season=item.season,
                        episode=item.episode,
                        target_path=item.target_path,
                        target_name=item.target_name,
                        blocked=item.blocked,
                        original_name=item.original_name,
                        action=item.action,
                        size=item.size,
                        other_size=item.other_size,
                        warning=item.warning,
                    )
                    for item in row.files
                ],
                empty_after=list(row.empty_after),
            )
            for row in report.series
        ],
        empty=[
            EmptyFolderOut(path=item.path, file_count=item.file_count, size=item.size, summary=item.summary)
            for item in report.empty
        ],
        mismatch=report.mismatch,
        duplicate_folders=report.duplicate_folders,
        unmatched=report.unmatched,
        empty_count=len(report.empty),
        suspect_files=[
            SuspectFileOut(
                path=item.path,
                name=item.name,
                size=item.size,
                media_id=item.media_id,
                message=item.message,
            )
            for item in report.suspect_files
        ],
    )


@router.get("/review", response_model=ReviewOut)
def library_review(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> ReviewOut:
    """Dry-run report. Folders, then the files inside them. Nothing is moved."""
    del _principal
    from mediatracker.library.review import build_review

    roots, protected = _empty_roots(db, request.app.state.settings)
    return _review_out(
        build_review(db, roots[0], roots[1], protected=protected, skip=_scan_skip(db, request.app.state.settings))
    )


@router.post("/review/files", response_model=ApplyOut)
def apply_review_files(
    body: ApplyFilesIn,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> ApplyOut:
    del _principal
    from mediatracker.core.storage import library_skip, storage_layout
    from mediatracker.library.audit import apply_audit

    movies, series = library_roots(db, request.app.state.settings)
    layout = storage_layout(db, request.app.state.settings)
    try:
        result = apply_audit(
            db,
            movies,
            series,
            body.paths,
            validated=body.validated,
            trash=layout.trash_dir,
            skip=library_skip(layout),
            settings=request.app.state.settings,
        )
    except LibraryPlanError as exc:
        raise Problem(409, "Conflit", exc.message) from exc
    if result.applied:
        db.commit()
    return ApplyOut(
        applied=result.applied,
        id=result.rename_id,
        moves=len(result.moves),
        skipped=len(result.skipped),
    )


@router.post("/review/merge", response_model=ApplyOut)
def apply_review_merge(
    body: ApplyMergeIn,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> ApplyOut:
    del _principal
    from mediatracker.library.review import apply_merge

    roots, protected = _empty_roots(db, request.app.state.settings)
    try:
        applied, rename_id, pending = apply_merge(
            db,
            roots[0],
            roots[1],
            body.key,
            validated=body.validated,
            protected=protected,
            skip=_scan_skip(db, request.app.state.settings),
        )
    except LibraryPlanError as exc:
        raise Problem(409, "Conflit", exc.message) from exc
    if applied:
        db.commit()
    return ApplyOut(applied=applied, id=rename_id, moves=len(pending), skipped=0)


def _regroup_group(group) -> RegroupGroupOut:
    return RegroupGroupOut(
        media_id=group.media_id,
        title=group.title,
        year=group.year,
        tmdb_id=group.tmdb_id,
        media_type=group.media_type,
        canonical=group.canonical,
        match=group.match,
        folders=list(group.folders),
        moves=[
            RegroupMoveOut(
                src=move.src,
                dest=move.dest,
                name=move.name,
                original_name=move.original_name,
                blocked=move.blocked,
                action=move.action,
                size=move.size,
                other_size=move.other_size,
                warning=move.warning,
            )
            for move in group.moves
        ],
        empty_after=list(group.empty_after),
        loose=group.loose,
    )


def _regroup_out(report, rename_id: int | None = None, *, applied: bool = False) -> RegroupOut:
    return RegroupOut(
        groups=[_regroup_group(group) for group in report.groups],
        unmatched=list(report.unmatched),
        ambiguous=list(report.ambiguous),
        empty=list(report.empty),
        loose=[_regroup_group(group) for group in report.loose],
        rename_id=rename_id,
        applied=applied,
    )


@router.get("/regroup", response_model=RegroupOut)
def library_regroup(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> RegroupOut:
    """Dry-run. Stray release folders mapped onto tracked fiches. Nothing is moved."""
    del _principal
    from mediatracker.library.regroup import plan_regroup

    roots, protected = _empty_roots(db, request.app.state.settings)
    return _regroup_out(
        plan_regroup(db, roots[0], roots[1], protected=protected, skip=_scan_skip(db, request.app.state.settings))
    )


@router.post("/regroup", response_model=RegroupOut)
def apply_library_regroup(
    body: RegroupIn,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> RegroupOut:
    """Apply the dry-run for the chosen fiches. ``validated`` must be true to move files."""
    del _principal
    from mediatracker.library.regroup import apply_regroup

    roots, protected = _empty_roots(db, request.app.state.settings)
    try:
        applied, _rename_id, report = apply_regroup(
            db,
            roots[0],
            roots[1],
            body.media_ids,
            validated=body.validated,
            protected=protected,
            include_loose=body.include_loose,
            skip=_scan_skip(db, request.app.state.settings),
            settings=request.app.state.settings,
        )
    except LibraryPlanError as exc:
        raise Problem(409, "Conflit", exc.message) from exc
    if applied:
        schedule_library_refresh(db, datetime.now(timezone.utc))
        db.commit()
    return _regroup_out(report, _rename_id, applied=applied)


def _incomplete_out(
    db: Session,
    snapshot,
    *,
    moved: int = 0,
    failed: int = 0,
    errors: tuple[str, ...] | list[str] = (),
    trash: str = "",
    trash_ids: tuple[int, ...] | list[int] = (),
) -> IncompleteOut:
    from mediatracker.core.incomplete import IncompleteSnapshot, cleanup_report

    if not isinstance(snapshot, IncompleteSnapshot):
        snapshot = IncompleteSnapshot(tuple(snapshot), 0, 0, sum(item.written_bytes for item in snapshot))
    report = cleanup_report(db)
    return IncompleteOut(
        files=[
            IncompleteFileOut(
                path=item.path,
                name=item.name,
                size=item.written_bytes or item.size,
                reason=item.reason,
                written_bytes=item.written_bytes,
                reserved_bytes=item.reserved_bytes,
                job_id=item.job_id,
                job_state=item.job_state,
                orphan=item.orphan,
            )
            for item in snapshot.files
        ],
        moved=moved,
        failed=failed,
        errors=list(errors),
        trash=trash,
        auto_enabled=report.enabled,
        last_run_at=report.at,
        auto_message=report.message,
        folder_written_bytes=snapshot.folder_written_bytes,
        folder_reserved_bytes=snapshot.folder_reserved_bytes,
        total_written_bytes=snapshot.total_written_bytes,
        trash_ids=list(trash_ids),
    )


@router.get("/incomplete", response_model=IncompleteOut)
def library_incomplete(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> IncompleteOut:
    """Recompute the incomplete folder. A due automatic pass runs first, at most once an hour."""
    del _principal
    from mediatracker.core.incomplete import incomplete_snapshot, maybe_clean_incomplete
    from mediatracker.core.storage import storage_layout

    settings = request.app.state.settings
    maybe_clean_incomplete(db, settings)
    return _incomplete_out(
        db,
        incomplete_snapshot(db, settings),
        trash=str(storage_layout(db, settings).trash_dir),
    )


@router.post("/incomplete", response_model=IncompleteOut)
def apply_library_incomplete(
    body: IncompleteIn,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> IncompleteOut:
    """Move the preview to the trash. ``validated`` must be true. Files are not unlinked."""
    del _principal
    from mediatracker.core.incomplete import apply_incomplete

    result = apply_incomplete(
        db,
        request.app.state.settings,
        validated=body.validated,
        paths=body.paths,
    )
    if result.moved:
        db.commit()
    from mediatracker.core.incomplete import IncompleteSnapshot

    return _incomplete_out(
        db,
        IncompleteSnapshot(
            result.files,
            result.folder_written_bytes,
            result.folder_reserved_bytes,
            result.total_written_bytes,
        ),
        moved=result.moved,
        failed=result.failed,
        errors=result.errors,
        trash=result.trash,
        trash_ids=result.trash_ids,
    )


@router.get("/poster")
def library_poster(
    path: str,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
):
    del _principal
    from mediatracker.library.review import local_poster_file

    movies, series = library_roots(db, request.app.state.settings)
    image = local_poster_file(Path(path), movies, series)
    if image is None:
        raise Problem(404, "Introuvable", "Aucune affiche dans ce dossier.")
    return FileResponse(image)


@router.get("/folders", response_model=FolderPage)
def library_folders(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> FolderPage:
    del _principal
    movies, series = library_roots(db, request.app.state.settings)
    skip = _scan_skip(db, request.app.state.settings)
    listed = list_folders(movies, series, skip=skip)
    media_index = _folder_media_index(db, listed)
    return FolderPage(items=[_folder_out(row, media_index=media_index) for row in listed])


@router.post("/folders/identify", response_model=IdentifyOut)
def identify_library_folder(
    body: IdentifyIn,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> IdentifyOut:
    del _principal
    settings = request.app.state.settings
    folder, _root, kind = _locate(db, settings, body.path)
    token = tmdb_token.read_tmdb_token(db, settings.secret_key)
    tmdb, owned_tmdb = _tmdb_for(token)
    jellyfin_items: list[dict] = []
    _url, api_key, user_id, _prefix, path_map = jellyfin_settings(db, settings)
    from mediatracker.integrations.jellyfin import JellyfinClient, JellyfinError

    client = None
    if _url and api_key and user_id:
        client = JellyfinClient(_url, api_key, user_id)
        try:
            from mediatracker.integrations.jellyfin import resolve_user_id

            user_id = resolve_user_id(client, user_id)
            jellyfin_items = client.user_items(user_id)
        except JellyfinError:
            jellyfin_items = []
    try:
        tmdb_id, confidence, source, suggestions = identify_folder(
            folder,
            media_type=kind,
            jellyfin_items=jellyfin_items,
            path_map=path_map,
            tmdb=tmdb,
        )
        if tmdb is not None and hasattr(tmdb, "glance"):
            suggestions = [_glanced(tmdb, item) for item in suggestions]
    except TmdbError:
        tmdb_id, confidence, source, suggestions = None, "none", "", []
    finally:
        if owned_tmdb and tmdb is not None:
            tmdb.close()
        if client is not None:
            client.close()
    title, year = folder.name, None
    from mediatracker.core.library_plan import _folder_title

    title, year = _folder_title(folder.name)
    return IdentifyOut(
        tmdb_id=tmdb_id,
        confidence=confidence,
        source=source,
        title=title,
        year=year,
        suggestions=[_suggestion_out(item) for item in suggestions],
    )


@router.post("/folders/rename", response_model=RenameOut)
def rename_library_folder(
    body: RenameIn,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> RenameOut:
    del _principal
    settings = request.app.state.settings
    folder, root, kind = _locate(db, settings, body.path)
    title = (body.title or "").strip()
    year = body.year
    if body.tmdb_id is not None:
        token = tmdb_token.read_tmdb_token(db, settings.secret_key)
        if token:
            client, owned = borrow_tmdb(token, TmdbClient)
            try:
                fetched = client.fetch(body.tmdb_id, kind)
            except TmdbError as exc:
                raise Problem(422, "TMDB", exc.message) from exc
            finally:
                if owned:
                    client.close()
            title = title or fetched.title
            year = year or fetched.year
    if not title:
        from mediatracker.core.library_plan import _folder_title

        title, guessed = _folder_title(folder.name)
        year = year or guessed
    try:
        row = rename_folder(
            db, folder, root=root, media_type=kind, title=title, year=year, tmdb_id=body.tmdb_id
        )
    except LibraryPlanError as exc:
        raise Problem(409, "Conflit", exc.message) from exc
    if row is None:
        return RenameOut(id=None, moves=0, already=True)
    schedule_library_refresh(db, datetime.now(timezone.utc))
    db.commit()
    return RenameOut(id=row.id, moves=len(row.moves or []))


@router.post("/folders/undo", response_model=RenameOut)
def undo_library_rename(
    body: UndoIn,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> RenameOut:
    del _principal
    try:
        row = undo_rename(db, body.id)
    except LibraryPlanError as exc:
        raise Problem(409, "Conflit", exc.message) from exc
    db.commit()
    return RenameOut(id=row.id, moves=len(row.moves or []))


class FollowFolderIn(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    follow: bool = True


class FollowFolderOut(BaseModel):
    media_id: int | None = None
    followed: bool
    attached: int = 0
    message: str


class EmptyFolderOut(BaseModel):
    path: str
    file_count: int
    size: int
    summary: str


class EmptyPage(BaseModel):
    paths: list[str]
    items: list[EmptyFolderOut] = []


class EmptyIn(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=200)


@router.post("/folders/follow", response_model=FollowFolderOut)
def follow_library_folder(
    body: FollowFolderIn,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> FollowFolderOut:
    """Follow or unfollow the fiche linked to a folder. Files stay either way."""
    del _principal
    settings = request.app.state.settings
    folder, _root, kind = _locate(db, settings, body.path)
    title, year, tag = folder.name, None, None
    for listed in list_folders(*library_roots(db, settings), skip=_scan_skip(db, settings)):
        if listed.path == str(folder.resolve()) or listed.name == folder.name:
            tag = listed.tmdb_id
            title = listed.title or title
            year = listed.year
            break
    media = None
    if tag is not None:
        media = db.scalar(select(Media).where(Media.tmdb_id == tag, Media.type == kind))
    if media is None and title:
        media = db.scalar(select(Media).where(Media.type == kind, Media.title == title))
    if not body.follow:
        if media is None or not media.followed:
            return FollowFolderOut(followed=False, message="Ce dossier n'est pas suivi.")
        from mediatracker.core.follow import unfollow

        unfollow(db, media, reason="folder", emit_event=False)
        db.commit()
        return FollowFolderOut(media_id=media.id, followed=False, message="Suivi arrêté. Les fichiers restent.")
    from mediatracker.library.scanner import attach_known_files

    if media is None:
        from mediatracker.api.v1.media import MediaCreate, insert_followed

        media, _episodes = insert_followed(
            db,
            settings.secret_key,
            MediaCreate(type=kind, title=title, year=year, tmdb_id=tag),
        )
    else:
        from mediatracker.core.follow import follow

        follow(db, None, media=media, source="folder")
    attached = attach_known_files(db, settings, media)
    db.commit()
    if attached:
        label = "épisodes" if kind == "series" else "fichiers"
        message = f"{attached} {label} déjà présents rattachés."
    else:
        message = "Suivi. Aucun fichier local à rattacher."
    return FollowFolderOut(media_id=media.id, followed=True, attached=attached, message=message)


def _trash_days(db: Session, default: int) -> int:
    row = db.get(Setting, "trash_retention_days")
    if row is None or not row.value:
        return default
    try:
        return int(row.value)
    except ValueError:
        return default


def _scan_skip(db: Session, settings) -> tuple[Path, ...]:
    """Downloads, the incomplete folder, and the trash. Never part of the library."""
    from mediatracker.core.storage import library_skip, storage_layout

    return library_skip(storage_layout(db, settings))


def _empty_roots(db: Session, settings):
    from mediatracker.core.storage import incomplete_dir, storage_layout

    layout = storage_layout(db, settings)
    protected = (
        layout.media_dir,
        layout.downloads_dir,
        incomplete_dir(layout),
        layout.trash_dir,
        settings.data_dir,
        settings.config_dir,
    )
    return (layout.movies_dir, layout.series_dir), protected


@router.get("/empty-folders", response_model=EmptyPage)
def list_empty_folders(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> EmptyPage:
    del _principal
    roots, protected = _empty_roots(db, request.app.state.settings)
    rows = preview_empty_folders(*roots, protected=protected)
    return EmptyPage(
        paths=[row.path for row in rows],
        items=[
            EmptyFolderOut(path=row.path, file_count=row.file_count, size=row.size, summary=row.summary)
            for row in rows
        ],
    )


@router.post("/empty-folders", response_model=EmptyPage)
def clean_empty_folders(
    body: EmptyIn,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> EmptyPage:
    del _principal
    roots, protected = _empty_roots(db, request.app.state.settings)
    from mediatracker.core.storage import storage_layout

    layout = storage_layout(db, request.app.state.settings)
    moved: list[TrashedFile] = []
    try:
        removed = remove_empty_folders(
            body.paths,
            *roots,
            protected=protected,
            trash=layout.trash_dir,
            moved=moved,
        )
    except LibraryPlanError as exc:
        raise Problem(422, "Dossiers vides", exc.message) from exc
    moment = datetime.now(timezone.utc)
    retention = _trash_days(db, request.app.state.settings.trash_retention_days)
    for item in moved:
        db.add(
            TrashEntry(
                original_path=item.original,
                trash_path=item.trash_path,
                size=item.size,
                label=Path(item.original).name[:300],
                reason="empty",
                deleted_at=moment,
                purge_after=moment + timedelta(days=retention),
            )
        )
    db.commit()
    return EmptyPage(paths=removed, items=[])


class BindIn(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    tmdb_id: int = Field(ge=1)
    type: str | None = Field(default=None, pattern="^(film|series)$")


class BindOut(BaseModel):
    path: str
    tmdb_id: int
    title: str
    attached: int
    media_id: int


def _glanced(tmdb: TmdbClient, item):
    from mediatracker.core.library_plan import _suggestion

    if item.poster_url and (item.season_count or item.runtime or item.overview):
        return item
    try:
        hit = tmdb.glance(item.tmdb_id, item.media_type)
    except TmdbError:
        return item
    return _suggestion(hit, item.media_type)


def _suggestion_out(item) -> SuggestionOut:
    return SuggestionOut(
        tmdb_id=item.tmdb_id,
        title=item.title,
        year=item.year,
        media_type=item.media_type,
        original_title=item.original_title,
        poster_url=item.poster_url,
        overview=item.overview,
        season_count=item.season_count,
        episode_count=item.episode_count,
        runtime=item.runtime,
    )


@router.post("/folders/bind", response_model=BindOut)
def bind_library_folder_route(
    body: BindIn,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> BindOut:
    """Set the TMDB id on a folder and attach the files already inside it."""
    del _principal
    from mediatracker.library.scanner import bind_library_folder

    settings = request.app.state.settings
    folder, _root, kind = _locate(db, settings, body.path)
    media_type = body.type or kind
    try:
        renamed, media, attached = bind_library_folder(
            db, settings, folder, tmdb_id=body.tmdb_id, media_type=media_type
        )
    except LibraryPlanError as exc:
        raise Problem(422, "Bibliothèque", exc.message) from exc
    db.commit()
    return BindOut(
        path=str(renamed),
        tmdb_id=body.tmdb_id,
        title=media.title,
        attached=attached,
        media_id=media.id,
    )


class JellyfinItemLink(BaseModel):
    item_id: int | None = None
    season: int | None = None
    episode: int | None = None
    jellyfin_item_id: str
    downloaded: bool
    watched: bool | None = None
    file_path: str | None = None
    title: str | None = None


class JellyfinIdentityChange(BaseModel):
    media_id: int
    title: str
    jellyfin_id: str
    from_tmdb: int | None = None
    to_tmdb: int | None = None
    name: str | None = None
    year: int | None = None
    items: list[JellyfinItemLink] = Field(default_factory=list)


class JellyfinMerge(BaseModel):
    keep_id: int
    drop_ids: list[int]
    jellyfin_id: str
    title: str
    tmdb_id: int | None = None


class JellyfinOrphan(BaseModel):
    media_id: int
    title: str


class JellyfinHold(BaseModel):
    """MediaTracker edited more recently. The id stays and a push carries it."""

    media_id: int
    title: str
    tmdb_id: int | None = None
    jellyfin_tmdb: int | None = None
    jellyfin_id: str


class JellyfinRename(BaseModel):
    media_id: int
    title: str
    src: str
    dest: str
    blocked: str | None = None
    jellyfin_id: str = ""


class JellyfinPush(BaseModel):
    media_id: int | None = None
    tmdb_id: int
    state: str
    attempts: int
    last_error: str | None = None
    max_attempts: int


class JellyfinAlignOut(BaseModel):
    scanning: bool
    progress: int | None = None
    message: str
    auto_rename: bool = False
    synced_at: str | None = None
    attaches: list[JellyfinIdentityChange] = Field(default_factory=list)
    reidentifies: list[JellyfinIdentityChange] = Field(default_factory=list)
    merges: list[JellyfinMerge] = Field(default_factory=list)
    orphans: list[JellyfinOrphan] = Field(default_factory=list)
    cleaned_after_watch: list[JellyfinOrphan] = Field(default_factory=list)
    holds: list[JellyfinHold] = Field(default_factory=list)
    renames: list[JellyfinRename] = Field(default_factory=list)
    pushes: list[JellyfinPush] = Field(default_factory=list)
    applied: bool = False
    moved: int = 0
    refresh: str | None = None
    refreshed: int = 0


class ValidatedIn(BaseModel):
    validated: bool = False


def _align_out(payload: dict) -> JellyfinAlignOut:
    return JellyfinAlignOut.model_validate(payload)


def _align_client(db: Session, settings):
    from mediatracker.library.jellyfin_align import open_client

    opened = open_client(db, settings)
    if opened is None:
        return None
    return opened[0]


@router.get("/jellyfin", response_model=JellyfinAlignOut)
def jellyfin_alignment(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> JellyfinAlignOut:
    """What would change if fiches followed Jellyfin. A running scan lists nothing."""
    del _principal
    from mediatracker.library.jellyfin_align import preview

    settings = request.app.state.settings
    client = _align_client(db, settings)
    try:
        return _align_out(preview(db, settings, client))
    finally:
        if client is not None:
            client.close()


@router.post("/jellyfin/refresh", response_model=JellyfinAlignOut)
def jellyfin_refresh(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
    full: bool = Query(default=False),
) -> JellyfinAlignOut:
    """Read Jellyfin into the cache. Incremental unless ``full`` is set. A scan is left untouched."""
    del _principal
    from mediatracker.library.jellyfin_align import AlignError, refresh_cache

    settings = request.app.state.settings
    client = _align_client(db, settings)
    if client is None:
        raise Problem(422, "Jellyfin", "Jellyfin n'est pas configuré.")
    token = tmdb_token.read_tmdb_token(db, settings.secret_key)
    tmdb, owned = _tmdb_for(token) if token else (None, False)
    try:
        try:
            payload = refresh_cache(db, settings, client, full=full, tmdb=tmdb)
        except AlignError as exc:
            raise Problem(409, "Jellyfin", exc.message) from exc
    finally:
        client.close()
        if owned and tmdb is not None:
            tmdb.close()
    if not payload.get("scanning"):
        db.commit()
    return _align_out(payload)


@router.post("/jellyfin/align", response_model=JellyfinAlignOut)
def jellyfin_apply_alignment(
    body: ValidatedIn,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> JellyfinAlignOut:
    """Apply identity only. Files are not moved."""
    del _principal
    from mediatracker.library.jellyfin_align import AlignError, apply_alignment

    settings = request.app.state.settings
    client = _align_client(db, settings)
    try:
        payload = apply_alignment(db, settings, client, validated=body.validated)
    except AlignError as exc:
        status = 409 if exc.code == "SCANNING" else 422
        raise Problem(status, "Jellyfin", exc.message) from exc
    finally:
        if client is not None:
            client.close()
    if body.validated:
        db.commit()
    return _align_out(payload)


@router.post("/jellyfin/rename", response_model=JellyfinAlignOut)
def jellyfin_apply_rename(
    body: ValidatedIn,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> JellyfinAlignOut:
    """Rename from the Jellyfin identity, then refresh those items. Watched state is restored if the rescan drops it."""
    del _principal
    from mediatracker.library.jellyfin_align import AlignError, apply_renames

    settings = request.app.state.settings
    client = _align_client(db, settings)
    try:
        payload = apply_renames(db, settings, client, validated=body.validated)
    except AlignError as exc:
        status = 409 if exc.code == "SCANNING" else 422
        raise Problem(status, "Jellyfin", exc.message) from exc
    finally:
        if client is not None:
            client.close()
    if body.validated:
        db.commit()
    return _align_out(payload)
