"""Home lists for « À voir »: unwatched files, this week's air dates, download queue."""

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session

from mediatracker.api.deps import get_db, require_scope
from mediatracker.core.catalog_list import followed_index, library_files_for_entries, list_catalog_page
from mediatracker.core.catalog_poster import display_poster_url
from mediatracker.core.display_title import clean_display_title
from mediatracker.core.release_title import release_caption
from mediatracker.core.tmdb_votes import exposed_tmdb_vote_average
from mediatracker.core.tracking import EpisodeView, progress_of, tmdb_page_url
from mediatracker.db.models import (
    CatalogEntry,
    DigitalRelease,
    Download,
    Event,
    LibraryFile,
    Media,
    MediaItem,
    SourceBinding,
)
from mediatracker.events.catalog import LIBRARY_IMPORTED

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

_ACTIVE = ("pending", "resolving", "queued", "downloading", "post_processing")
_BUSY = ("pending", "resolving", "queued", "downloading", "post_processing", "failed", "done")
_LIMIT = 50


class EpisodeCard(BaseModel):
    media_id: int
    media_title: str
    media_type: str
    tmdb_id: int | None
    on_hold: bool
    series_status: str | None
    item_id: int | None
    season: int | None
    episode: int | None
    title: str | None
    air_date: date | None
    downloaded: bool
    watched: bool
    poster_url: str | None = None
    still_url: str | None = None
    year: int | None = None
    overview: str | None = None


class DownloadCard(BaseModel):
    id: int
    state: str
    progress: int
    speed: int | None = None
    eta_seconds: int | None = None
    filename: str | None
    error_code: str | None
    error: str | None
    media_id: int | None
    media_title: str | None
    item_id: int | None
    season: int | None
    episode: int | None


class ProgressCard(BaseModel):
    media_id: int
    media_title: str
    media_type: str
    watched: int
    total: int
    poster_url: str | None = None
    last_watched_at: datetime | None = None
    year: int | None = None
    overview: str | None = None
    tmdb_id: int | None = None


class AvailableCard(BaseModel):
    media_id: int
    media_title: str
    media_type: str
    item_id: int
    season: int | None
    episode: int | None
    title: str | None
    poster_url: str | None = None
    year: int | None = None
    overview: str | None = None
    tmdb_id: int | None = None


class WatchGroup(BaseModel):
    media_id: int
    media_title: str
    media_type: str
    count: int
    label: str
    poster_url: str | None = None
    year: int | None = None
    overview: str | None = None
    tmdb_id: int | None = None
    items: list[EpisodeCard]


class LatestCard(BaseModel):
    title: str
    subtitle: str | None = None
    poster_url: str | None = None
    media_type: str
    media_id: int | None = None
    year: int | None = None
    overview: str | None = None
    tmdb_id: int | None = None
    tmdb_url: str | None = None
    catalog_id: int | None = None
    followed: bool = False
    source_url: str | None = None
    in_library: bool = False
    ignored: bool = False
    hive_work_key: str | None = None
    tmdb_vote_average: float | None = None


class RecentDownloadCard(BaseModel):
    media_id: int
    media_title: str
    media_type: str
    season: int | None
    episode: int | None
    title: str | None
    episode_count: int = 1
    poster_url: str | None = None
    downloaded_at: datetime
    year: int | None = None
    overview: str | None = None
    tmdb_id: int | None = None


class UpcomingReleaseCard(BaseModel):
    title: str
    poster_url: str | None = None
    overview: str | None = None
    media_type: str
    release_on: date
    tmdb_id: int
    tmdb_url: str | None = None


class ToSortItemOut(BaseModel):
    media_id: int
    media_title: str
    poster_url: str | None = None
    kind: str
    detail: str
    highlight: str | None = None
    season: int | None = None
    seasons: list[int] = Field(default_factory=list)
    ignore_key: str


class ToSortGroupOut(BaseModel):
    kind: str
    title: str
    items: list[ToSortItemOut]


class ToSortOut(BaseModel):
    total: int
    footer: str
    groups: list[ToSortGroupOut]


class ToSortIgnoreIn(BaseModel):
    ignore_key: str = Field(min_length=1, max_length=120)


class DashboardOut(BaseModel):
    to_watch: list[EpisodeCard]
    to_watch_groups: list[WatchGroup]
    upcoming_week: list[EpisodeCard]
    available: list[AvailableCard]
    downloads_active: list[DownloadCard]
    downloads_failed: list[DownloadCard]
    progress: list[ProgressCard]
    latest: list[LatestCard] = []
    recent_downloads: list[RecentDownloadCard] = []
    upcoming_releases: list[UpcomingReleaseCard] = []
    catalog_films: list[LatestCard] = []
    catalog_series: list[LatestCard] = []
    hive_dispos: list[LatestCard] = []


def _card_from_item(media: Media, item: MediaItem) -> EpisodeCard:
    return EpisodeCard(
        media_id=media.id,
        media_title=_shown(media.title),
        media_type=media.type,
        tmdb_id=media.tmdb_id,
        on_hold=media.on_hold,
        series_status=media.series_status,
        item_id=item.id,
        season=item.season,
        episode=item.episode,
        title=_shown(item.title) or None,
        air_date=item.air_date,
        downloaded=item.downloaded,
        watched=item.watched,
        poster_url=media.poster_url,
        still_url=item.still_url,
        year=media.year,
        overview=media.overview,
    )


def _card_from_next(media: Media) -> EpisodeCard:
    return EpisodeCard(
        media_id=media.id,
        media_title=_shown(media.title),
        media_type=media.type,
        tmdb_id=media.tmdb_id,
        on_hold=media.on_hold,
        series_status=media.series_status,
        item_id=None,
        season=media.next_episode_season,
        episode=media.next_episode_number,
        title=_shown(media.next_episode_title) or None,
        air_date=media.next_air_date,
        downloaded=False,
        watched=False,
        poster_url=media.poster_url,
        year=media.year,
        overview=media.overview,
    )


@router.get("/to-sort", response_model=ToSortOut)
def dashboard_to_sort(
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> ToSortOut:
    del _principal
    from mediatracker.core.to_sort import compute_to_sort, snapshot_to_api

    return ToSortOut.model_validate(snapshot_to_api(compute_to_sort(db)))


@router.post("/to-sort/ignore")
def dashboard_to_sort_ignore(
    body: ToSortIgnoreIn,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> dict[str, bool]:
    del _principal
    from mediatracker.api.errors import Problem
    from mediatracker.core.to_sort import remember_ignore, validate_ignore_key

    key = body.ignore_key.strip()
    if not validate_ignore_key(key):
        raise Problem(422, "Requête invalide", "Clé d'ignoré invalide.")
    remember_ignore(db, key)
    db.commit()
    return {"ok": True}


@router.get("", response_model=DashboardOut)
def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> DashboardOut:
    del _principal
    settings = request.app.state.settings
    from mediatracker.core.to_watch import to_watch_pairs

    to_watch_rows = to_watch_pairs(db, limit=_LIMIT)
    today = date.today()
    week_end = today + timedelta(days=7)
    from mediatracker.core.media_hide import media_visible_clause

    upcoming_rows = db.execute(
        select(MediaItem, Media)
        .join(Media, Media.id == MediaItem.media_id)
        .where(
            MediaItem.air_date.is_not(None),
            MediaItem.air_date >= today,
            MediaItem.air_date < week_end,
            media_visible_clause(),
        )
        .order_by(MediaItem.air_date, MediaItem.season, MediaItem.episode)
        .limit(_LIMIT)
    ).all()
    seen = {(item.media_id, item.season, item.episode) for item, _media in upcoming_rows}
    upcoming = [_card_from_item(media, item) for item, media in upcoming_rows]
    announced = db.scalars(
        select(Media).where(
            Media.next_air_date.is_not(None),
            Media.next_air_date >= today,
            Media.next_air_date < week_end,
            media_visible_clause(),
        )
    ).all()
    for media in announced:
        key = (media.id, media.next_episode_season, media.next_episode_number)
        if key in seen:
            continue
        upcoming.append(_card_from_next(media))
    upcoming.sort(key=lambda card: (card.air_date or date.max, card.media_title, card.episode or 0))

    def _downloads(states: tuple[str, ...] | None, failed: bool) -> list[DownloadCard]:
        stmt = (
            select(Download, MediaItem, Media)
            .join(MediaItem, MediaItem.id == Download.media_item_id)
            .join(Media, Media.id == MediaItem.media_id)
        )
        if failed:
            stmt = stmt.where(Download.state == "failed").order_by(Download.id.desc())
        else:
            stmt = stmt.where(Download.state.in_(states or ())).order_by(Download.priority.desc(), Download.id)
        cards = []
        for download, item, media in db.execute(stmt.limit(_LIMIT)).all():
            cards.append(
                DownloadCard(
                    id=download.id,
                    state=download.state,
                    progress=download.progress,
                    speed=download.speed,
                    eta_seconds=download.eta_seconds,
                    filename=download.filename,
                    error_code=download.error_code,
                    error=download.error,
                    media_id=media.id,
                    media_title=_shown(media.title),
                    item_id=item.id,
                    season=item.season,
                    episode=item.episode,
                )
            )
        return cards

    watched_cards = [_card_from_item(media, item) for item, media in to_watch_rows]
    latest_cards, catalog_films, catalog_series = _catalog_home(db, settings)
    from mediatracker.hive.home_row import list_hive_dispos_for_home

    hive_dispos = [LatestCard.model_validate(row) for row in list_hive_dispos_for_home(db, settings, limit=20)]
    return DashboardOut(
        to_watch=watched_cards,
        to_watch_groups=_watch_groups(watched_cards),
        upcoming_week=upcoming[:_LIMIT],
        available=_available(db),
        downloads_active=_downloads(_ACTIVE, failed=False),
        downloads_failed=_downloads(None, failed=True),
        progress=_progress(db),
        latest=latest_cards,
        recent_downloads=_recent_downloads(db),
        upcoming_releases=_upcoming_releases(db),
        catalog_films=catalog_films,
        catalog_series=catalog_series,
        hive_dispos=hive_dispos,
    )


def _code(card: EpisodeCard) -> str:
    if card.season is None or card.episode is None:
        return "Film"
    return f"S{card.season:02d}E{card.episode:02d}"


def _watch_groups(cards: list[EpisodeCard]) -> list[WatchGroup]:
    ordered: dict[int, list[EpisodeCard]] = {}
    for card in cards:
        ordered.setdefault(card.media_id, []).append(card)
    groups: list[WatchGroup] = []
    for bucket in ordered.values():
        bucket.sort(key=lambda card: (card.season or 0, card.episode or 0, card.item_id or 0))
        first = bucket[0]
        count = len(bucket)
        if first.media_type == "film" or (count == 1 and first.season is None):
            label = "1 film non vu"
        elif count == 1:
            label = f"1 épisode non vu – {_code(first)}"
        else:
            label = f"{count} épisodes non vus – {_code(bucket[0])}→{_code(bucket[-1])}"
        groups.append(
            WatchGroup(
                media_id=first.media_id,
                media_title=first.media_title,
                media_type=first.media_type,
                count=count,
                label=label,
            poster_url=first.poster_url,
            year=first.year,
            overview=first.overview,
            tmdb_id=first.tmdb_id,
            items=bucket,
        )
        )
    return groups


def _available(db: Session) -> list[AvailableCard]:
    """Source found, still worth offering.

    A Jellyfin watched flag, an unfollowed fiche, and a film already obtained
    stay out. When a series has active season bindings, only those seasons
    are selected for tracking.
    """
    busy = set(
        db.scalars(select(Download.media_item_id).where(Download.state.in_(_BUSY))).all()
    )
    on_shelf = exists(
        select(LibraryFile.id).where(
            LibraryFile.media_item_id == MediaItem.id,
            LibraryFile.status == "matched",
            LibraryFile.missing_at.is_(None),
        )
    )
    season_selected = exists(
        select(SourceBinding.id).where(
            SourceBinding.media_id == Media.id,
            SourceBinding.active.is_(True),
            SourceBinding.season.is_not(None),
        )
    )
    this_season = exists(
        select(SourceBinding.id).where(
            SourceBinding.media_id == Media.id,
            SourceBinding.active.is_(True),
            SourceBinding.season == MediaItem.season,
        )
    )
    film_obtained = and_(
        Media.type == "film",
        or_(
            and_(Media.last_poll_result.is_not(None), Media.last_poll_result == "obtained"),
            MediaItem.file_path.is_not(None),
            on_shelf,
        ),
    )
    from mediatracker.core.media_hide import media_visible_clause

    rows = db.execute(
        select(MediaItem, Media)
        .join(Media, Media.id == MediaItem.media_id)
        .where(
            MediaItem.source_available.is_(True),
            MediaItem.downloaded.is_(False),
            MediaItem.watched.is_(False),
            Media.followed.is_(True),
            media_visible_clause(),
            ~film_obtained,
            or_(
                Media.type != "series",
                MediaItem.season.is_(None),
                ~season_selected,
                this_season,
            ),
        )
        .order_by(Media.title, MediaItem.season, MediaItem.episode)
        .limit(_LIMIT)
    ).all()
    cards: list[AvailableCard] = []
    for item, media in rows:
        if item.id in busy:
            continue
        chosen = media.tracked_seasons
        if media.type == "series" and chosen is not None and item.season not in {int(number) for number in chosen}:
            continue
        cards.append(
            AvailableCard(
                media_id=media.id,
                media_title=_shown(media.title),
                media_type=media.type,
                item_id=item.id,
                season=item.season,
                episode=item.episode,
                title=_shown(item.title) or None,
                poster_url=media.poster_url,
                year=media.year,
                overview=media.overview,
                tmdb_id=media.tmdb_id,
            )
        )
    return cards


def _progress_complete(media: Media, episodes: list[MediaItem]) -> bool:
    """True when nothing remains to track on the home Progression row."""
    if media.type == "film":
        film = next((item for item in episodes if item.season is None and item.episode is None), None)
        return film is not None and film.watched
    if media.type == "series":
        from mediatracker.core.series_follow import series_all_aired_episodes_watched

        return series_all_aired_episodes_watched(episodes)
    return False


def _progress(db: Session) -> list[ProgressCard]:
    from mediatracker.core.media_hide import media_visible_clause

    media_rows = list(
        db.scalars(
            select(Media).where(Media.followed.is_(True), media_visible_clause()).order_by(Media.title, Media.id)
        ).all()
    )
    if not media_rows:
        return []
    grouped: dict[int, list[MediaItem]] = {row.id: [] for row in media_rows}
    items = db.scalars(select(MediaItem).where(MediaItem.media_id.in_(grouped))).all()
    for item in items:
        grouped.setdefault(item.media_id, []).append(item)
    cards: list[ProgressCard] = []
    for media in media_rows:
        episodes = grouped.get(media.id, [])
        if not episodes:
            continue
        if _progress_complete(media, episodes):
            continue
        progress = progress_of(
            [
                EpisodeView(
                    season=item.season,
                    episode=item.episode,
                    title=item.title,
                    air_date=item.air_date,
                    downloaded=item.downloaded,
                    watched=item.watched,
                    source_available=item.source_available,
                )
                for item in episodes
            ]
        )
        if progress.total == 0:
            continue
        watched_at = [item.watched_at for item in episodes if item.watched_at is not None]
        cards.append(
            ProgressCard(
                media_id=media.id,
                media_title=_shown(media.title),
                media_type=media.type,
                watched=progress.watched,
                total=progress.total,
                poster_url=media.poster_url,
                last_watched_at=max(watched_at) if watched_at else None,
                year=media.year,
                overview=media.overview,
                tmdb_id=media.tmdb_id,
            )
        )
    return cards


def _shown(value: str | None) -> str:
    return clean_display_title(value) or (value or "").strip()


class _CatalogContext:
    def __init__(
        self,
        *,
        fiche_tmdb: dict[tuple[str, int], int],
        fiche_title: dict[tuple[str, str, int | None], int],
        followed_tmdb: set[tuple[str, int]],
        followed_title: set[tuple[str, str, int | None]],
        library_files: list[LibraryFile],
        media_votes: dict[int, tuple[float | None, int | None]],
    ) -> None:
        self.fiche_tmdb = fiche_tmdb
        self.fiche_title = fiche_title
        self.followed_tmdb = followed_tmdb
        self.followed_title = followed_title
        self.library_files = library_files
        self.media_votes = media_votes


def _latest_card(row: CatalogEntry, ctx: _CatalogContext) -> LatestCard:
    from mediatracker.core.catalog_list import in_library, is_followed

    title, version = release_caption(row.title, quality=row.quality, language=row.language, season=row.season)
    media_id = _catalog_fiche(row, ctx.fiche_tmdb, ctx.fiche_title)
    followed = is_followed(row, ctx.followed_tmdb, ctx.followed_title)
    vote_average = None
    if media_id is not None:
        stored = ctx.media_votes.get(media_id, (None, None))
        vote_average = exposed_tmdb_vote_average(stored[0], stored[1])
    return LatestCard(
        title=title,
        subtitle=version,
        poster_url=display_poster_url(row),
        media_type=row.media_type,
        media_id=media_id,
        tmdb_vote_average=vote_average,
        year=row.year,
        overview=row.overview,
        tmdb_id=row.tmdb_id,
        tmdb_url=tmdb_page_url(row.media_type, row.tmdb_id),
        catalog_id=row.id,
        followed=followed,
        source_url=row.source_url,
        in_library=in_library(row, ctx.library_files),
        ignored=row.ignored,
    )


def _catalog_home(db: Session, settings) -> tuple[list[LatestCard], list[LatestCard], list[LatestCard]]:
    from mediatracker.providers.sources.loading import any_latest_source_usable

    if not any_latest_source_usable(db, settings):
        return [], [], []
    fiche_tmdb, fiche_title, media_votes = _fiche_lookup(db)
    followed_tmdb, followed_title = followed_index(db)
    followed_sets = (followed_tmdb, followed_title)
    film_page = list_catalog_page(
        db,
        settings,
        media_type="film",
        q=None,
        genre=None,
        year=None,
        followed=None,
        in_library_flag=None,
        include_ignored=False,
        limit=_LIMIT,
        followed_sets=followed_sets,
    )
    series_page = list_catalog_page(
        db,
        settings,
        media_type="series",
        q=None,
        genre=None,
        year=None,
        followed=None,
        in_library_flag=None,
        include_ignored=False,
        limit=_LIMIT,
        followed_sets=followed_sets,
    )
    latest_rows = list(
        db.scalars(
            select(CatalogEntry)
            .where(CatalogEntry.ignored.is_(False))
            .order_by(CatalogEntry.first_seen_at.desc(), CatalogEntry.id.desc())
            .limit(8)
        ).all()
    )
    library_rows = [*film_page.rows, *series_page.rows, *latest_rows]
    from mediatracker.core.media_hide import hidden_media_ids

    hidden_ids = hidden_media_ids(db)
    ctx = _CatalogContext(
        fiche_tmdb=fiche_tmdb,
        fiche_title=fiche_title,
        followed_tmdb=followed_tmdb,
        followed_title=followed_title,
        library_files=library_files_for_entries(db, library_rows),
        media_votes=media_votes,
    )

    def _home_catalog_card(row: CatalogEntry) -> LatestCard | None:
        media_id = _catalog_fiche(row, ctx.fiche_tmdb, ctx.fiche_title)
        if media_id is not None and media_id in hidden_ids:
            return None
        return _latest_card(row, ctx)

    latest_cards = [card for row in latest_rows if (card := _home_catalog_card(row)) is not None]
    catalog_films = [card for row in film_page.rows if (card := _home_catalog_card(row)) is not None]
    catalog_series = [card for row in series_page.rows if (card := _home_catalog_card(row)) is not None]
    return latest_cards, catalog_films, catalog_series


def _recent_downloads(db: Session) -> list[RecentDownloadCard]:
    """Real imports only (``library.imported``), grouped by series fiche."""
    from mediatracker.core.file_status import FileStatusIndex

    events = list(
        db.scalars(
            select(Event)
            .where(Event.type == LIBRARY_IMPORTED, Event.item_id.is_not(None))
            .order_by(Event.ts.desc(), Event.id.desc())
            .limit(_LIMIT * 4)
        ).all()
    )
    if not events:
        return []
    item_ids = [row.item_id for row in events if row.item_id is not None]
    items = {
        row.id: row
        for row in db.scalars(select(MediaItem).where(MediaItem.id.in_(item_ids))).all()
    }
    media_ids = {item.media_id for item in items.values()}
    medias = {row.id: row for row in db.scalars(select(Media).where(Media.id.in_(media_ids))).all()}
    status = FileStatusIndex.load(db, list(items.values()))
    grouped: dict[int, dict] = {}
    ordered: list[int] = []
    for event in events:
        item = items.get(event.item_id or -1)
        if item is None:
            continue
        if status.for_item(item) != "present":
            continue
        media = medias.get(item.media_id)
        if media is None:
            continue
        bucket = grouped.get(media.id)
        if bucket is None:
            bucket = {"media": media, "ts": event.ts, "items": [], "seen": set()}
            grouped[media.id] = bucket
            ordered.append(media.id)
        if item.id in bucket["seen"]:
            continue
        bucket["seen"].add(item.id)
        bucket["items"].append(item)
        if event.ts > bucket["ts"]:
            bucket["ts"] = event.ts
    cards: list[RecentDownloadCard] = []
    for media_id in ordered:
        if len(cards) >= _LIMIT:
            break
        bucket = grouped[media_id]
        media = bucket["media"]
        episodes = bucket["items"]
        moment = bucket["ts"]
        sample = episodes[0]
        cards.append(
            RecentDownloadCard(
                media_id=media.id,
                media_title=_shown(media.title),
                media_type=media.type,
                season=sample.season if len(episodes) == 1 else None,
                episode=sample.episode if len(episodes) == 1 else None,
                title=_shown(sample.title) or None if len(episodes) == 1 else None,
                episode_count=len(episodes),
                poster_url=media.poster_url,
                downloaded_at=moment,
                year=media.year,
                overview=media.overview,
                tmdb_id=media.tmdb_id,
            )
        )
    return cards


def _upcoming_releases(db: Session) -> list[UpcomingReleaseCard]:
    today = date.today()
    rows = list(
        db.scalars(
            select(DigitalRelease)
            .where(DigitalRelease.release_on >= today)
            .order_by(DigitalRelease.release_on, DigitalRelease.title, DigitalRelease.id)
            .limit(_LIMIT * 3)
        ).all()
    )
    if not rows:
        return []
    from mediatracker.core.media_hide import media_visible_clause

    followed = {
        (media_type, tmdb_id)
        for media_type, tmdb_id in db.execute(
            select(Media.type, Media.tmdb_id).where(
                Media.followed.is_(True),
                Media.tmdb_id.is_not(None),
                media_visible_clause(),
            )
        ).all()
    }
    cards: list[UpcomingReleaseCard] = []
    for row in rows:
        if (row.media_type, row.tmdb_id) in followed:
            continue
        cards.append(
            UpcomingReleaseCard(
                title=_shown(row.title),
                poster_url=row.poster_url,
                overview=(row.overview or "").strip() or None,
                media_type=row.media_type,
                release_on=row.release_on,
                tmdb_id=row.tmdb_id,
                tmdb_url=tmdb_page_url(row.media_type, row.tmdb_id),
            )
        )
        if len(cards) >= _LIMIT:
            break
    return cards


def _fiche_lookup(
    db: Session,
) -> tuple[
    dict[tuple[str, int], int],
    dict[tuple[str, str, int | None], int],
    dict[int, tuple[float | None, int | None]],
]:
    from mediatracker.core.catalog import fold_title

    media_rows = db.execute(
        select(
            Media.id,
            Media.type,
            Media.tmdb_id,
            Media.title,
            Media.year,
            Media.tmdb_vote_average,
            Media.tmdb_vote_count,
        ).order_by(Media.id)
    ).all()
    by_tmdb: dict[tuple[str, int], int] = {}
    by_title: dict[tuple[str, str, int | None], int] = {}
    media_votes: dict[int, tuple[float | None, int | None]] = {}
    for media_id, media_type, tmdb_id, title, year, vote_average, vote_count in media_rows:
        media_votes[media_id] = (vote_average, vote_count)
        if tmdb_id:
            by_tmdb.setdefault((media_type, tmdb_id), media_id)
        by_title.setdefault((media_type, fold_title(title), year), media_id)
    return by_tmdb, by_title, media_votes


def _catalog_fiche(
    row: CatalogEntry,
    by_tmdb: dict[tuple[str, int], int],
    by_title: dict[tuple[str, str, int | None], int],
) -> int | None:
    from mediatracker.core.catalog import fold_title

    if row.tmdb_id and (row.media_type, row.tmdb_id) in by_tmdb:
        return by_tmdb[(row.media_type, row.tmdb_id)]
    return by_title.get((row.media_type, fold_title(row.title), row.year))
