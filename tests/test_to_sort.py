"""Bloc Accueil « À trier » — trois signalements et persistance des ignorés."""

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from tests.conftest import csrf, login

from mediatracker.core.media_hide import hide_media
from mediatracker.core.seasons import (
    apply_new_seasons,
    maybe_refresh_seasons,
    maybe_refresh_unfollowed_enrichment,
    sync_unfollowed_series_seasons,
)
from mediatracker.core.to_sort import compute_to_sort, load_ignored, remember_ignore, validate_ignore_key
from mediatracker.db.models import Event, LibraryFile, Media, MediaItem, MediaSeason
from mediatracker.events import record_event
from mediatracker.integrations.tmdb import TmdbEpisode, TmdbSeason, TmdbTitle

NOW = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
TODAY = date(2026, 6, 15)
STALE_SEEN = NOW - timedelta(days=45)


def _series(
    db,
    *,
    title: str,
    followed: bool,
    tmdb_id: int,
    series_status: str | None = None,
) -> Media:
    row = Media(
        type="series",
        title=title,
        year=2090,
        tmdb_id=tmdb_id,
        followed=followed,
        series_status=series_status,
    )
    db.add(row)
    db.flush()
    return row


def _renewed_lines(snapshot):
    for group in snapshot.groups:
        if group.kind == "renewed":
            return list(group.items)
    return []


def _renewed_season_numbers(snapshot) -> set[int]:
    return {line.season for line in _renewed_lines(snapshot) if line.season is not None}


def _episode(
    db,
    media: Media,
    *,
    season: int,
    episode: int,
    watched: bool,
    air: date,
    watched_at=None,
    seen_at=None,
    downloaded_at=None,
):
    item = MediaItem(
        media_id=media.id,
        season=season,
        episode=episode,
        title=f"Épisode démo {season}x{episode}",
        air_date=air,
        downloaded=True,
        watched=watched,
        watched_at=watched_at,
        downloaded_at=downloaded_at if downloaded_at is not None else STALE_SEEN,
        file_path=f"/data/{media.id}-s{season:02d}e{episode:02d}.mkv",
    )
    db.add(item)
    db.flush()
    db.add(
        LibraryFile(
            path=item.file_path,
            media_type="series",
            status="matched",
            media_item_id=item.id,
            seen_at=seen_at if seen_at is not None else STALE_SEEN,
        )
    )
    return item


def test_renewed_via_unfollowed_enrichment(client, app, monkeypatch):
    login(client)
    db = app.state.session_factory()
    settings = app.state.settings
    media_id = 0
    try:
        media = _series(db, title="Série renouvelée démo", followed=False, tmdb_id=770001)
        media_id = media.id
        db.add(MediaSeason(media_id=media.id, number=1, air_date=date(2020, 1, 1), episode_count=10))
        _episode(db, media, season=1, episode=1, watched=True, air=date(2020, 1, 1), watched_at=NOW)
        record_event(db, "media.unfollowed", media_id=media.id, when=NOW, payload={"title": media.title})
        db.commit()
    finally:
        db.close()

    season3 = TmdbSeason(3, "Saison 3", None, date(2027, 3, 12), 10, 303, ())
    fetched = TmdbTitle(
        770001,
        "series",
        "Série renouvelée démo",
        2090,
        None,
        None,
        None,
        None,
        (season3,),
    )

    class _Fetch:
        def fetch(self, tmdb_id: int, media_type: str):
            return fetched

        def close(self) -> None:
            return None

    monkeypatch.setattr("mediatracker.core.seasons._open", lambda db, s: _Fetch())
    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: "tok")

    db = app.state.session_factory()
    try:
        count = maybe_refresh_unfollowed_enrichment(db, settings, now=NOW)
        assert count == 1
        row = db.scalar(
            select(MediaSeason).where(MediaSeason.media_id == media_id, MediaSeason.number == 3)
        )
        assert row is not None
        assert (
            db.scalar(select(Event.id).where(Event.type == "season.announced", Event.media_id == media_id))
            is None
        )
        assert (
            db.scalar(select(MediaItem.id).where(MediaItem.media_id == media_id, MediaItem.season == 3))
            is None
        )
    finally:
        db.close()

    payload = client.get("/api/v1/dashboard/to-sort").json()
    assert payload["total"] == 1
    item = payload["groups"][0]["items"][0]
    assert item["kind"] == "renewed"
    assert item["highlight"] == "12 mars 2027"
    assert "Saison 3" in item["detail"]
    assert "Vous ne la suivez plus" in item["detail"]


def test_renewed_neutral_when_never_followed(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        media = _series(db, title="Série jamais suivie", followed=False, tmdb_id=770010)
        db.add(MediaSeason(media_id=media.id, number=1, air_date=date(2025, 1, 1), episode_count=6))
        _episode(db, media, season=1, episode=1, watched=False, air=date(2025, 1, 1))
        db.add(MediaSeason(media_id=media.id, number=2, air_date=date(2027, 1, 1), episode_count=8))
        db.commit()
    finally:
        db.close()
    payload = client.get("/api/v1/dashboard/to-sort").json()
    item = payload["groups"][0]["items"][0]
    assert "Vous ne la suivez plus" not in item["detail"]


def test_renewed_started_season_wording(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        media = _series(db, title="Série en cours démo", followed=False, tmdb_id=770011)
        db.add(MediaSeason(media_id=media.id, number=1, air_date=date(2025, 6, 1), episode_count=6))
        _episode(db, media, season=1, episode=1, watched=False, air=date(2025, 6, 1))
        started = date.today() - timedelta(days=20)
        db.add(MediaSeason(media_id=media.id, number=2, air_date=started, episode_count=8))
        db.commit()
    finally:
        db.close()
    payload = client.get("/api/v1/dashboard/to-sort").json()
    renewed = next(
        item
        for group in payload["groups"]
        for item in group["items"]
        if item["kind"] == "renewed"
    )
    detail = renewed["detail"]
    assert "en cours" in detail
    assert "annoncée pour le" not in detail


def test_to_sort_unwatched_season_on_disk(client, app):
    login(client)
    db = app.state.session_factory()
    media_id = 0
    try:
        media = _series(db, title="Série disque démo", followed=True, tmdb_id=770002)
        media_id = media.id
        _episode(db, media, season=2, episode=1, watched=False, air=date(2025, 1, 1))
        _episode(db, media, season=2, episode=2, watched=False, air=date(2025, 1, 8))
        db.commit()
    finally:
        db.close()
    payload = client.get("/api/v1/dashboard/to-sort").json()
    kinds = {item["kind"] for group in payload["groups"] for item in group["items"]}
    assert "unwatched_season" in kinds
    row = next(
        item for group in payload["groups"] for item in group["items"] if item["kind"] == "unwatched_season"
    )
    assert row["ignore_key"] == f"unwatched:{media_id}:2"


def test_unwatched_hidden_when_files_recent(client, app):
    login(client)
    db = app.state.session_factory()
    recent = datetime.now(timezone.utc) - timedelta(days=2)
    try:
        media = _series(db, title="Série fichiers récents", followed=True, tmdb_id=770012)
        _episode(
            db,
            media,
            season=1,
            episode=1,
            watched=False,
            air=date(2025, 1, 1),
            seen_at=STALE_SEEN,
            downloaded_at=recent,
        )
        db.commit()
    finally:
        db.close()
    payload = client.get("/api/v1/dashboard/to-sort").json()
    kinds = {item["kind"] for group in payload["groups"] for item in group["items"]}
    assert "unwatched_season" not in kinds


def test_to_sort_stop_follow_idle(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        media = _series(db, title="Série en pause démo", followed=True, tmdb_id=770003)
        idle = NOW - timedelta(days=220)
        _episode(db, media, season=1, episode=1, watched=True, air=date(2024, 1, 1), watched_at=idle)
        for number in (2, 3, 4, 5):
            _episode(db, media, season=1, episode=number, watched=False, air=date(2024, 2, number))
        db.commit()
    finally:
        db.close()
    payload = client.get("/api/v1/dashboard/to-sort").json()
    stop = next(
        row
        for group in payload["groups"]
        for row in group["items"]
        if row["kind"] == "stop_follow" and row["media_title"] == "Série en pause démo"
    )
    assert stop["highlight"]
    assert "épisodes en attente" in stop["detail"]


def test_to_sort_ignore_durable(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        media = _series(db, title="Série ignorée démo", followed=False, tmdb_id=770004)
        db.add(MediaSeason(media_id=media.id, number=1, air_date=date(2025, 1, 1), episode_count=6))
        _episode(db, media, season=1, episode=1, watched=True, air=date(2025, 1, 1), watched_at=NOW)
        db.add(MediaSeason(media_id=media.id, number=2, air_date=date(2027, 1, 1), episode_count=8))
        db.commit()
    finally:
        db.close()
    first = client.get("/api/v1/dashboard/to-sort").json()
    key = first["groups"][0]["items"][0]["ignore_key"]
    denied = client.post(
        "/api/v1/dashboard/to-sort/ignore",
        json={"ignore_key": key},
        headers=csrf(client),
    )
    assert denied.status_code == 200
    second = client.get("/api/v1/dashboard/to-sort").json()
    assert second["total"] == 0
    db = app.state.session_factory()
    try:
        assert key in load_ignored(db)
    finally:
        db.close()


def test_ignore_rejects_invalid_key(client, app):
    login(client)
    response = client.post(
        "/api/v1/dashboard/to-sort/ignore",
        json={"ignore_key": "bad-key"},
        headers=csrf(client),
    )
    assert response.status_code == 422


def test_ignore_purges_deleted_media(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        media = _series(db, title="Série purge démo", followed=False, tmdb_id=770013)
        db.add(MediaSeason(media_id=media.id, number=1, air_date=date(2028, 1, 1), episode_count=6))
        db.commit()
        remember_ignore(db, f"renewed:{media.id}:1", moment=NOW)
        db.delete(media)
        db.commit()
        assert load_ignored(db) == set()
    finally:
        db.close()


def test_renewed_ignore_key_includes_season(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        media = _series(db, title="Série saison ignorée", followed=False, tmdb_id=770005)
        db.add(MediaSeason(media_id=media.id, number=3, air_date=date(2026, 1, 1), episode_count=6))
        _episode(db, media, season=3, episode=1, watched=True, air=date(2026, 1, 1), watched_at=NOW)
        db.add(MediaSeason(media_id=media.id, number=4, air_date=date(2027, 5, 1), episode_count=6))
        db.commit()
        snapshot = compute_to_sort(db, today=TODAY)
        line = snapshot.groups[0].items[0]
        remember_ignore(db, line.ignore_key, moment=NOW)
        db.commit()
        assert f"renewed:{media.id}:4" in load_ignored(db)
        db.add(MediaSeason(media_id=media.id, number=5, air_date=date(2028, 1, 1), episode_count=6))
        db.commit()
        again = compute_to_sort(db, today=TODAY)
        assert again.total == 1
        assert again.groups[0].items[0].season == 5
    finally:
        db.close()


def test_unwatched_ignores_recent_seen_at_uses_downloaded_at(client, app):
    login(client)
    db = app.state.session_factory()
    media_id = 0
    try:
        media = _series(db, title="Série vu scanner", followed=True, tmdb_id=770014)
        media_id = media.id
        recent_seen = datetime.now(timezone.utc)
        _episode(
            db,
            media,
            season=3,
            episode=1,
            watched=False,
            air=date(2025, 6, 1),
            seen_at=recent_seen,
            downloaded_at=STALE_SEEN,
        )
        _episode(
            db,
            media,
            season=3,
            episode=2,
            watched=False,
            air=date(2025, 6, 8),
            seen_at=recent_seen,
            downloaded_at=STALE_SEEN,
        )
        db.commit()
    finally:
        db.close()
    payload = client.get("/api/v1/dashboard/to-sort").json()
    row = next(
        (
            item
            for group in payload["groups"]
            for item in group["items"]
            if item["kind"] == "unwatched_season" and item["media_id"] == media_id
        ),
        None,
    )
    assert row is not None
    assert row["season"] == 3


def test_hidden_media_excluded_from_to_sort(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        media = _series(db, title="Série masquée", followed=False, tmdb_id=770015)
        db.add(MediaSeason(media_id=media.id, number=2, air_date=date(2028, 4, 1), episode_count=8))
        hide_media(media, moment=NOW)
        db.commit()
        assert compute_to_sort(db, today=TODAY).total == 0
    finally:
        db.close()
    payload = client.get("/api/v1/dashboard/to-sort").json()
    assert payload["total"] == 0


def test_apply_new_seasons_backfills_known_empty_season(client, app):
    del client
    db = app.state.session_factory()
    try:
        media = _series(db, title="Série reprise", followed=False, tmdb_id=770016)
        db.add(MediaSeason(media_id=media.id, number=1, air_date=date(2024, 1, 1), episode_count=8))
        db.commit()
        sync_unfollowed_series_seasons(
            db,
            media,
            TmdbTitle(
                770016,
                "series",
                media.title,
                2090,
                None,
                None,
                None,
                None,
                (TmdbSeason(3, "S3", None, date(2027, 5, 1), 2, 3, ()),),
            ),
            NOW,
        )
        db.commit()
        assert (
            db.scalar(select(MediaItem.id).where(MediaItem.media_id == media.id, MediaItem.season == 3)) is None
        )
        media.followed = True
        db.commit()
        media_id = media.id
        ep1 = TmdbEpisode(3, 1, "Épisode 1", date(2027, 5, 1), 301, None)
        ep2 = TmdbEpisode(3, 2, "Épisode 2", date(2027, 5, 8), 302, None)
        fetched = TmdbTitle(
            770016,
            "series",
            media.title,
            2090,
            None,
            None,
            None,
            None,
            (TmdbSeason(3, "S3", None, date(2027, 5, 1), 2, 3, (ep1, ep2)),),
        )
        assert apply_new_seasons(db, media, fetched, NOW) == []
        db.commit()
        assert (
            db.scalar(
                select(Event.id).where(
                    Event.type == "season.announced",
                    Event.media_id == media_id,
                    Event.dedupe_key == f"season.announced:{media_id}:3",
                )
            )
            is None
        )
        rows = list(
            db.scalars(
                select(MediaItem).where(MediaItem.media_id == media_id, MediaItem.season == 3).order_by(
                    MediaItem.episode
                )
            ).all()
        )
        assert len(rows) == 2
        assert rows[0].episode == 1 and rows[1].episode == 2
    finally:
        db.close()


def test_follow_refresh_backfills_after_unfollowed_sync(client, app, monkeypatch):
    del client
    db = app.state.session_factory()
    settings = app.state.settings
    try:
        media = _series(db, title="Série refresh", followed=False, tmdb_id=770017)
        db.add(MediaSeason(media_id=media.id, number=1, air_date=date(2020, 1, 1), episode_count=6))
        db.commit()
        ep1 = TmdbEpisode(4, 1, "Épisode 1", date(2028, 1, 1), 401, None)
        season4 = TmdbSeason(4, "S4", None, date(2028, 1, 10), 1, 4, (ep1,))
        fetched = TmdbTitle(
            770017,
            "series",
            media.title,
            2090,
            None,
            None,
            None,
            None,
            (season4,),
        )
        sync_unfollowed_series_seasons(db, media, fetched, NOW)
        media_id = media.id
        media.followed = True
        media.tmdb_refreshed_at = NOW - timedelta(days=2)
        db.commit()
    finally:
        db.close()

    class _Fetch:
        def fetch(self, tmdb_id: int, media_type: str):
            return fetched

        def close(self) -> None:
            return None

    monkeypatch.setattr("mediatracker.core.seasons._open", lambda db, s: _Fetch())
    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: "tok")
    db = app.state.session_factory()
    try:
        assert maybe_refresh_seasons(db, settings, now=NOW) == 0
        row = db.scalar(select(MediaItem).where(MediaItem.media_id == media_id, MediaItem.season == 4))
        assert row is not None and row.episode == 1
    finally:
        db.close()


def test_apply_new_seasons_leaves_populated_season_unchanged(client, app):
    del client
    db = app.state.session_factory()
    try:
        media = _series(db, title="Série stable", followed=True, tmdb_id=770018)
        db.add(MediaSeason(media_id=media.id, number=1, air_date=date(2024, 1, 1), episode_count=2))
        db.commit()
        _episode(db, media, season=1, episode=1, watched=False, air=date(2024, 1, 1))
        _episode(db, media, season=1, episode=2, watched=False, air=date(2024, 1, 8))
        db.commit()
        before = list(db.scalars(select(MediaItem).where(MediaItem.media_id == media.id)).all())
        ep1 = TmdbEpisode(1, 1, "Épisode 1", date(2024, 1, 1), 101, None)
        ep2 = TmdbEpisode(1, 2, "Épisode 2", date(2024, 1, 8), 102, None)
        fetched = TmdbTitle(
            770018,
            "series",
            media.title,
            2090,
            None,
            None,
            None,
            None,
            (TmdbSeason(1, "S1", None, date(2024, 1, 1), 2, 1, (ep1, ep2)),),
        )
        announced = apply_new_seasons(db, media, fetched, NOW)
        db.commit()
        after = list(db.scalars(select(MediaItem).where(MediaItem.media_id == media.id)).all())
        assert len(after) == len(before) == 2
        assert announced == []
    finally:
        db.close()


def test_double_refresh_future_empty_season_no_announce(client, app):
    del client
    db = app.state.session_factory()
    try:
        media = _series(db, title="Série double refresh", followed=True, tmdb_id=770019)
        db.add(MediaSeason(media_id=media.id, number=1, air_date=date(2024, 1, 1), episode_count=6))
        db.add(
            MediaSeason(
                media_id=media.id,
                number=5,
                air_date=date(2028, 6, 1),
                episode_count=10,
            )
        )
        db.commit()
        media_id = media.id
        fetched = TmdbTitle(
            770019,
            "series",
            media.title,
            2090,
            None,
            None,
            None,
            None,
            (TmdbSeason(5, "S5", None, date(2028, 6, 1), 10, 5, ()),),
        )
        assert apply_new_seasons(db, media, fetched, NOW) == []
        db.commit()
        assert apply_new_seasons(db, media, fetched, NOW) == []
        db.commit()
        assert (
            db.scalar(
                select(Event.id).where(Event.type == "season.announced", Event.media_id == media_id)
            )
            is None
        )
    finally:
        db.close()


def test_old_empty_season_not_backfilled(client, app):
    del client
    db = app.state.session_factory()
    try:
        media = _series(db, title="Série ancienne", followed=True, tmdb_id=770020)
        old_day = NOW.date() - timedelta(days=200)
        db.add(
            MediaSeason(media_id=media.id, number=7, air_date=old_day, episode_count=8)
        )
        db.commit()
        ep1 = TmdbEpisode(7, 1, "Épisode 1", old_day, 701, None)
        fetched = TmdbTitle(
            770020,
            "series",
            media.title,
            2090,
            None,
            None,
            None,
            None,
            (TmdbSeason(7, "S7", None, old_day, 8, 7, (ep1,)),),
        )
        assert apply_new_seasons(db, media, fetched, NOW) == []
        db.commit()
        assert (
            db.scalar(select(MediaItem.id).where(MediaItem.media_id == media.id, MediaItem.season == 7))
            is None
        )
    finally:
        db.close()


def test_renewed_excludes_ended_old_season(client, app):
    del client
    db = app.state.session_factory()
    try:
        media = _series(
            db,
            title="Série terminée ancienne",
            followed=False,
            tmdb_id=770100,
            series_status="ended",
        )
        db.add(
            MediaSeason(
                media_id=media.id,
                number=1,
                air_date=date(2025, 6, 15),
                episode_count=8,
            )
        )
        _episode(
            db,
            media,
            season=1,
            episode=1,
            watched=True,
            air=date(2025, 6, 15),
            watched_at=NOW,
        )
        db.commit()
        snapshot = compute_to_sort(db, today=TODAY)
        assert _renewed_season_numbers(snapshot) == set()
    finally:
        db.close()


def test_renewed_future_season_after_watched_prior(client, app):
    del client
    db = app.state.session_factory()
    try:
        media = _series(db, title="Série suite future", followed=False, tmdb_id=770101)
        db.add(MediaSeason(media_id=media.id, number=1, air_date=date(2025, 1, 1), episode_count=6))
        _episode(db, media, season=1, episode=1, watched=True, air=date(2025, 1, 1), watched_at=NOW)
        db.add(MediaSeason(media_id=media.id, number=2, air_date=date(2027, 2, 1), episode_count=8))
        db.commit()
        snapshot = compute_to_sort(db, today=TODAY)
        assert _renewed_season_numbers(snapshot) == {2}
    finally:
        db.close()


def test_renewed_absent_when_season_already_seen(client, app):
    del client
    db = app.state.session_factory()
    try:
        media = _series(db, title="Série saison déjà vue", followed=False, tmdb_id=770102)
        db.add(MediaSeason(media_id=media.id, number=1, air_date=date(2025, 1, 1), episode_count=6))
        _episode(db, media, season=1, episode=1, watched=True, air=date(2025, 1, 1), watched_at=NOW)
        db.add(MediaSeason(media_id=media.id, number=2, air_date=date(2027, 3, 1), episode_count=8))
        _episode(db, media, season=2, episode=1, watched=True, air=date(2027, 3, 1), watched_at=NOW)
        db.commit()
        snapshot = compute_to_sort(db, today=TODAY)
        assert 2 not in _renewed_season_numbers(snapshot)
    finally:
        db.close()


def test_renewed_recent_started_season(client, app):
    del client
    db = app.state.session_factory()
    try:
        media = _series(db, title="Série saison récente", followed=False, tmdb_id=770103)
        db.add(MediaSeason(media_id=media.id, number=1, air_date=date(2025, 1, 1), episode_count=6))
        _episode(db, media, season=1, episode=1, watched=True, air=date(2025, 1, 1), watched_at=NOW)
        db.add(MediaSeason(media_id=media.id, number=2, air_date=date(2026, 5, 26), episode_count=8))
        db.commit()
        snapshot = compute_to_sort(db, today=TODAY)
        assert _renewed_season_numbers(snapshot) == {2}
    finally:
        db.close()


def test_renewed_stale_started_season_absent(client, app):
    del client
    db = app.state.session_factory()
    try:
        media = _series(db, title="Série saison trop ancienne", followed=False, tmdb_id=770104)
        db.add(MediaSeason(media_id=media.id, number=1, air_date=date(2025, 1, 1), episode_count=6))
        _episode(db, media, season=1, episode=1, watched=True, air=date(2025, 1, 1), watched_at=NOW)
        db.add(MediaSeason(media_id=media.id, number=2, air_date=date(2026, 3, 17), episode_count=8))
        db.commit()
        snapshot = compute_to_sort(db, today=TODAY)
        assert _renewed_season_numbers(snapshot) == set()
    finally:
        db.close()


def test_renewed_never_had_nothing_absent(client, app):
    del client
    db = app.state.session_factory()
    try:
        media = _series(db, title="Série sans historique", followed=False, tmdb_id=770105)
        db.add(MediaSeason(media_id=media.id, number=2, air_date=date(2028, 1, 1), episode_count=6))
        db.commit()
        snapshot = compute_to_sort(db, today=TODAY)
        assert _renewed_season_numbers(snapshot) == set()
    finally:
        db.close()


def test_sync_unfollowed_series_seasons_no_items(client, app):
    del client
    db = app.state.session_factory()
    try:
        media = _series(db, title="Sync démo", followed=False, tmdb_id=88)
        db.add(MediaSeason(media_id=media.id, number=1, air_date=date(2020, 1, 1), episode_count=6))
        db.commit()
        fetched = TmdbTitle(
            88,
            "series",
            "Sync démo",
            2090,
            None,
            None,
            None,
            None,
            (TmdbSeason(2, "S2", None, date(2027, 6, 1), 8, 2, ()),),
        )
        sync_unfollowed_series_seasons(db, media, fetched, NOW)
        db.commit()
        assert validate_ignore_key(f"renewed:{media.id}:2")
        assert (
            db.scalar(select(MediaItem.id).where(MediaItem.media_id == media.id, MediaItem.season == 2)) is None
        )
    finally:
        db.close()