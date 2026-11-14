"""Liste catalogue : filtres, tri, total et page en SQL."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import and_, exists, false, func, literal, or_, select, tuple_
from sqlalchemy.orm import Session, load_only

from mediatracker.core.catalog import _filter_value, fold_title
from mediatracker.db.models import CatalogEntry, LibraryFile, Media


@dataclass(frozen=True)
class CatalogListResult:
    rows: list[CatalogEntry]
    total: int
    genres: list[str]
    years: list[int]


def followed_index(db: Session) -> tuple[set[tuple[str, int]], set[tuple[str, str, int | None]]]:
    rows = db.execute(
        select(Media.type, Media.tmdb_id, Media.title, Media.year).where(Media.followed.is_(True))
    ).all()
    by_tmdb = {(media_type, tmdb_id) for media_type, tmdb_id, _title, _year in rows if tmdb_id}
    by_title = {(media_type, fold_title(title), year) for media_type, _tmdb, title, year in rows}
    return by_tmdb, by_title


def library_files(db: Session) -> list[LibraryFile]:
    return list(
        db.scalars(
            select(LibraryFile).where(
                LibraryFile.status == "matched",
                LibraryFile.missing_at.is_(None),
            )
        ).all()
    )


def library_files_for_entries(db: Session, entries: list[CatalogEntry]) -> list[LibraryFile]:
    """Matched library rows that may cover these catalog entries (bounded, columns for matching only)."""
    if not entries:
        return []
    tmdb_ids = {entry.tmdb_id for entry in entries if entry.tmdb_id}
    types_for_title = {entry.media_type for entry in entries if not entry.tmdb_id}
    match_parts: list[object] = []
    if tmdb_ids:
        match_parts.append(LibraryFile.tmdb_id.in_(tmdb_ids))
    if types_for_title:
        match_parts.append(LibraryFile.media_type.in_(types_for_title))
    if not match_parts:
        return []
    return list(
        db.scalars(
            select(LibraryFile)
            .options(
                load_only(
                    LibraryFile.media_type,
                    LibraryFile.tmdb_id,
                    LibraryFile.parsed_title,
                    LibraryFile.parsed_year,
                    LibraryFile.season,
                    LibraryFile.episode,
                )
            )
            .where(
                LibraryFile.status == "matched",
                LibraryFile.missing_at.is_(None),
                or_(*match_parts),
            )
        ).all()
    )


def is_followed(
    row: CatalogEntry,
    by_tmdb: set[tuple[str, int]],
    by_title: set[tuple[str, str, int | None]],
) -> bool:
    if row.tmdb_id and (row.media_type, row.tmdb_id) in by_tmdb:
        return True
    return (row.media_type, row.title_folded or fold_title(row.title), row.year) in by_title


def year_compatible(left: int | None, right: int | None) -> bool:
    return left is None or right is None or left == right


def in_library(row: CatalogEntry, files: list[LibraryFile]) -> bool:
    folded = row.title_folded or fold_title(row.title)
    for item in files:
        if item.media_type != row.media_type:
            continue
        same_tmdb = bool(row.tmdb_id and item.tmdb_id == row.tmdb_id)
        same_title = fold_title(item.parsed_title) == folded and year_compatible(row.year, item.parsed_year)
        if not same_tmdb and not same_title:
            continue
        if row.media_type == "film" or row.season is None:
            return True
        if item.season != row.season:
            continue
        if row.episode is None or item.episode is None or item.episode == row.episode:
            return True
    return False


def _year_compatible_sql(left, right: int | None):
    if right is None:
        return or_(left.is_(None), literal(True))
    return or_(left.is_(None), left == right)


def _followed_clause(
    by_tmdb: set[tuple[str, int]],
    by_title: set[tuple[str, str, int | None]],
    *,
    want: bool,
) -> object:
    if not by_tmdb and not by_title:
        return false() if want else literal(True)
    parts = []
    if by_tmdb:
        parts.append(tuple_(CatalogEntry.media_type, CatalogEntry.tmdb_id).in_(list(by_tmdb)))
    if by_title:
        parts.append(
            tuple_(CatalogEntry.media_type, CatalogEntry.title_folded, CatalogEntry.year).in_(list(by_title))
        )
    matched = or_(*parts)
    return matched if want else ~matched


def _library_file_clause(item: LibraryFile) -> object:
    folded = fold_title(item.parsed_title)
    identity = or_(
        and_(CatalogEntry.tmdb_id.isnot(None), CatalogEntry.tmdb_id == item.tmdb_id),
        and_(
            CatalogEntry.title_folded == folded,
            _year_compatible_sql(CatalogEntry.year, item.parsed_year),
        ),
    )
    if item.episode is None:
        episode_ok: object = literal(True)
    else:
        episode_ok = or_(CatalogEntry.episode.is_(None), CatalogEntry.episode == item.episode)
    season_branch = and_(
        CatalogEntry.media_type == literal("series"),
        CatalogEntry.season.isnot(None),
        CatalogEntry.season == item.season,
        episode_ok,
    )
    return and_(
        CatalogEntry.media_type == item.media_type,
        identity,
        or_(
            CatalogEntry.media_type == literal("film"),
            CatalogEntry.season.is_(None),
            season_branch,
        ),
    )


def _library_clause(files: list[LibraryFile], *, want: bool) -> object:
    if not files:
        return false() if want else literal(True)
    matched = or_(*(_library_file_clause(item) for item in files))
    return matched if want else ~matched


def genre_matches(genre: str) -> object:
    needle = genre.casefold()
    genre_row = func.json_each(CatalogEntry.genres).table_valued("value")
    return exists(select(literal(1)).select_from(genre_row).where(func.lower(genre_row.c.value) == needle))


def _base_criteria(
    *,
    media_type: str,
    include_ignored: bool,
    quality: str | None,
    language: str | None,
) -> list[object]:
    criteria: list[object] = [CatalogEntry.media_type == media_type]
    if not include_ignored:
        criteria.append(CatalogEntry.ignored.is_(False))
    if quality:
        criteria.append(CatalogEntry.quality == quality)
    if language:
        criteria.append(CatalogEntry.language == language)
    return criteria


def _apply_list_filters(
    criteria: list[object],
    *,
    needle: str,
    genre_text: str,
    year: int | None,
    followed: bool | None,
    in_library_flag: bool | None,
    by_tmdb: set[tuple[str, int]],
    by_title: set[tuple[str, str, int | None]],
    files: list[LibraryFile],
) -> list[object]:
    filters = list(criteria)
    if year is not None:
        filters.append(CatalogEntry.year == year)
    if genre_text:
        filters.append(genre_matches(genre_text))
    if needle:
        filters.append(CatalogEntry.title_folded.contains(needle, autoescape=True))
    if followed is not None:
        filters.append(_followed_clause(by_tmdb, by_title, want=followed))
    if in_library_flag is not None:
        filters.append(_library_clause(files, want=in_library_flag))
    return filters


def _collect_facets(db: Session, criteria: list[object]) -> tuple[list[str], list[int]]:
    genres: set[str] = set()
    years: set[int] = set()
    stmt = select(CatalogEntry.genres, CatalogEntry.year).where(*criteria)
    for genres_json, year_value in db.execute(stmt):
        if year_value is not None:
            years.add(year_value)
        if genres_json:
            for name in genres_json:
                if name:
                    genres.add(str(name))
    return sorted(genres), sorted(years, reverse=True)


def list_catalog_page(
    db: Session,
    settings,
    *,
    media_type: str,
    q: str | None,
    genre: str | None,
    year: int | None,
    followed: bool | None,
    in_library_flag: bool | None,
    include_ignored: bool,
    limit: int,
    followed_sets: tuple[set[tuple[str, int]], set[tuple[str, str, int | None]]] | None = None,
) -> CatalogListResult:
    from mediatracker.core.catalog_source_visibility import visible_latest_source_types

    visible_sources = visible_latest_source_types(db, settings)
    if not visible_sources:
        return CatalogListResult(rows=[], total=0, genres=[], years=[])
    quality = _filter_value(db, "catalog_quality", settings.catalog_quality)
    language = _filter_value(db, "catalog_language", settings.catalog_language)
    base_criteria = _base_criteria(
        media_type=media_type,
        include_ignored=include_ignored,
        quality=quality,
        language=language,
    )
    base_criteria.append(CatalogEntry.source.in_(visible_sources))
    genres, years = _collect_facets(db, base_criteria)

    if followed_sets is None:
        by_tmdb, by_title = followed_index(db)
    else:
        by_tmdb, by_title = followed_sets
    files = library_files(db) if in_library_flag is not None else []
    needle = fold_title(q or "")
    genre_text = (genre or "").strip()

    list_criteria = _apply_list_filters(
        base_criteria,
        needle=needle,
        genre_text=genre_text,
        year=year,
        followed=followed,
        in_library_flag=in_library_flag,
        by_tmdb=by_tmdb,
        by_title=by_title,
        files=files,
    )

    total = db.scalar(select(func.count()).select_from(CatalogEntry).where(*list_criteria)) or 0
    page_stmt = (
        select(CatalogEntry)
        .where(*list_criteria)
        .order_by(CatalogEntry.first_seen_at.desc(), CatalogEntry.id.desc())
        .limit(limit)
    )
    rows = list(db.scalars(page_stmt).all())
    return CatalogListResult(rows=rows, total=total, genres=genres, years=years)
