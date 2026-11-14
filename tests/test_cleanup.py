"""Cleanup rules, trash, and path safety on a temporary directory."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from tests.conftest import csrf, login

from mediatracker.core.cleanup import atomic_move, classify, explain_film, explain_media, maintenance, preview
from mediatracker.core.seasons import apply_new_seasons
from mediatracker.core.source_poll import Slot, _apply
from mediatracker.db.models import Download, Event, LibraryFile, Media, MediaItem, MediaSeason, TrashEntry
from mediatracker.integrations.jellyfin import JellyfinWatch
from mediatracker.integrations.jellyfin_sync import apply_played
from mediatracker.integrations.tmdb import TmdbEpisode, TmdbSeason, TmdbTitle
from mediatracker.library.scanner import scan_library
from mediatracker.providers.base import LinkCandidate


def _enable(client):
    login(client)
    saved = client.patch(
        "/api/v1/settings",
        json={
            "values": {
                "auto_delete_days": 14,
                "cleanup_notice_hours": 24,
                "trash_retention_days": 7,
            }
        },
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text


def _media(db, **kwargs):
    row = Media(type=kwargs.pop("type", "film"), title=kwargs.pop("title", "Film"), **kwargs)
    db.add(row)
    db.flush()
    return row


def _file(root: Path, name: str, payload: bytes = b"video") -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _item(db, media, path: Path | None, *, season=None, episode=None, watched_at=None, keep=False, watched=True):
    row = MediaItem(
        media_id=media.id,
        season=season,
        episode=episode,
        title="épisode" if episode else media.title,
        state="downloaded" if path else "missing",
        downloaded=path is not None,
        downloaded_at=watched_at,
        watched=watched,
        watched_at=watched_at if watched else None,
        file_path=str(path) if path else None,
        keep=keep,
    )
    db.add(row)
    db.flush()
    return row


def test_auto_cleanup_rules_notice_and_keep(client, app):
    _enable(client)
    now = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
    data = Path(app.state.settings.data_dir)
    movies = data / "media" / "Films"
    series = data / "media" / "Series"
    db = app.state.session_factory()
    try:
        recent = _file(movies, "Recent/Recent.mkv", b"r" * 10)
        old = _file(movies, "Old/Old.mkv", b"o" * 50)
        kept = _file(movies, "Kept/Kept.mkv", b"k" * 10)
        off = _file(movies, "Off/Off.mkv", b"f" * 10)
        recent_media = _media(db, title="Recent", auto_delete=True)
        old_media = _media(db, title="Old", auto_delete=True)
        kept_media = _media(db, title="Kept", auto_delete=True, keep=True)
        off_media = _media(db, title="Off", keep=True)
        _item(db, recent_media, recent, watched_at=now - timedelta(days=1))
        _item(db, old_media, old, watched_at=now - timedelta(days=20))
        _item(db, kept_media, kept, watched_at=now - timedelta(days=20))
        _item(db, off_media, off, watched_at=now - timedelta(days=40))

        show = _media(db, type="series", title="Série", auto_delete=True)
        partial_a = _file(series, "Show/Season 01/a.mkv", b"a" * 20)
        partial_b = _file(series, "Show/Season 01/b.mkv", b"b" * 20)
        _item(db, show, partial_a, season=1, episode=1, watched_at=now - timedelta(days=30))
        _item(db, show, partial_b, season=1, episode=2, watched_at=None, watched=False)

        full = _media(db, type="series", title="Complète", auto_delete=True)
        full_a = _file(series, "Full/Season 01/a.mkv", b"a" * 30)
        full_b = _file(series, "Full/Season 01/b.mkv", b"b" * 40)
        keep_ep = _file(series, "Full/Season 02/keep.mkv", b"k" * 15)
        drop_ep = _file(series, "Full/Season 02/drop.mkv", b"d" * 25)
        season_keep = _file(series, "Full/Season 03/all.mkv", b"s" * 12)
        _item(db, full, full_a, season=1, episode=1, watched_at=now - timedelta(days=30))
        _item(db, full, full_b, season=1, episode=2, watched_at=now - timedelta(days=16))
        _item(db, full, keep_ep, season=2, episode=1, watched_at=now - timedelta(days=30), keep=True)
        _item(db, full, drop_ep, season=2, episode=2, watched_at=now - timedelta(days=30))
        _item(db, full, season_keep, season=3, episode=1, watched_at=now - timedelta(days=30))
        db.add(MediaSeason(media_id=full.id, number=3, keep=True))
        db.commit()

        due, upcoming = preview(db, app.state.settings, now=now)
        assert due == []
        upcoming_titles = {item.plan.media.title for item in upcoming}
        assert "Old" in upcoming_titles
        assert "Recent" not in upcoming_titles
        assert "Kept" not in upcoming_titles
        assert "Off" not in upcoming_titles
        assert "Série" not in upcoming_titles
        assert recent.is_file() and old.is_file() and partial_a.is_file()

        maintenance(db, app.state.settings, now=now)
        assert old.is_file()
        later = now + timedelta(hours=25)
        maintenance(db, app.state.settings, now=later)
        assert not old.is_file()
        assert recent.is_file() and kept.is_file() and off.is_file()
        assert partial_a.is_file() and partial_b.is_file()
        assert not full_a.is_file() and not full_b.is_file()
        assert keep_ep.is_file() and not drop_ep.is_file()
        assert season_keep.is_file()
        old_item = db.scalar(select(MediaItem).where(MediaItem.media_id == old_media.id))
        assert old_item.downloaded is False
        assert old_item.watched is True
        assert old_item.file_path is None
        held = db.scalar(select(TrashEntry).where(TrashEntry.original_path == str(old.resolve())))
        assert held is not None
        assert held.reason == "auto"
        assert Path(held.trash_path).is_file()
    finally:
        db.close()


def test_auto_cleanup_applies_to_unfollowed_series(client, app):
    _enable(client)
    now = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
    data = Path(app.state.settings.data_dir)
    movies = data / "media" / "Films"
    series = data / "media" / "Series"
    db = app.state.session_factory()
    try:
        film_path = _file(movies, "Solo/Solo.mkv", b"f" * 40)
        film = _media(db, title="Solo", auto_delete=True, followed=False)
        _item(db, film, film_path, watched_at=now - timedelta(days=20))
        show = _media(db, type="series", title="Non suivie", auto_delete=True, followed=False)
        episode = _file(series, "Show/Season 01/a.mkv", b"a" * 30)
        _item(db, show, episode, season=1, episode=1, watched_at=now - timedelta(days=20))
        assert film.followed is False and show.followed is False
        db.commit()
        _due, upcoming = preview(db, app.state.settings, now=now)
        titles = {item.plan.media.title for item in upcoming}
        assert titles == {"Solo", "Non suivie"}
        maintenance(db, app.state.settings, now=now)
        assert film_path.is_file() and episode.is_file()
        maintenance(db, app.state.settings, now=now + timedelta(hours=25))
        assert not film_path.is_file()
        assert not episode.is_file()
        reasons = {row.reason for row in db.scalars(select(TrashEntry)).all()}
        assert reasons == {"auto"}
    finally:
        db.close()


def test_trash_move_restore_purge_and_scanner(client, app):
    _enable(client)
    now = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
    data = Path(app.state.settings.data_dir)
    movies = data / "media" / "Films"
    db = app.state.session_factory()
    try:
        path = _file(movies, "Solo/Solo.mkv", b"z" * 80)
        media = _media(db, title="Solo", tmdb_id=7)
        item = _item(db, media, path, watched_at=now - timedelta(days=2))
        library = LibraryFile(
            path=str(path.resolve()),
            size=80,
            parsed_title="Solo",
            media_type="film",
            media_id=media.id,
            media_item_id=item.id,
            status="matched",
            confidence="high",
            seen_at=now,
        )
        db.add(library)
        db.commit()
        media_id = media.id
        item_id = item.id
    finally:
        db.close()

    db = app.state.session_factory()
    try:
        media_row = db.get(Media, media_id)
        media_row.keep = True
        db.commit()
    finally:
        db.close()
    removed = client.post(
        "/api/v1/cleanup/files",
        json={"media_id": media_id, "scope": "media", "confirm": False},
        headers=csrf(client),
    )
    assert removed.status_code == 422
    assert path.is_file()
    db = app.state.session_factory()
    try:
        media_row = db.get(Media, media_id)
        media_row.keep = False
        db.commit()
    finally:
        db.close()
    removed = client.post(
        "/api/v1/cleanup/files",
        json={"media_id": media_id, "scope": "media", "confirm": False},
        headers=csrf(client),
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["bytes"] == 80
    assert not path.is_file()
    db = app.state.session_factory()
    try:
        item = db.get(MediaItem, item_id)
        assert item.downloaded is False
        assert item.watched is True
        report = scan_library(db, movies=movies, series=data / "media" / "Series", now=now)
        assert report.missing == 0
        row = db.get(LibraryFile, library.id)
        assert row.status == "trashed"
        entry_id = removed.json()["entries"][0]["id"]
    finally:
        db.close()

    listed = client.get("/api/v1/trash")
    assert listed.status_code == 200
    assert listed.json()["trash_bytes"] == 80
    assert listed.json()["reclaimed_bytes"] == 0
    restored = client.post(f"/api/v1/trash/{entry_id}/restore", headers=csrf(client))
    assert restored.status_code == 200, restored.text
    assert path.is_file()
    db = app.state.session_factory()
    try:
        item = db.get(MediaItem, item_id)
        assert item.downloaded is True
        assert item.watched is True
        assert item.file_path == str(path.resolve())
    finally:
        db.close()

    removed = client.post(
        "/api/v1/cleanup/files",
        json={"media_id": media_id, "scope": "episode", "item_id": item_id, "confirm": True},
        headers=csrf(client),
    )
    assert removed.status_code == 200, removed.text
    entry_id = removed.json()["entries"][0]["id"]
    db = app.state.session_factory()
    try:
        entry = db.get(TrashEntry, entry_id)
        entry.purge_after = now - timedelta(days=1)
        db.commit()
        maintenance(db, app.state.settings, now=now)
        db.refresh(entry)
        assert entry.purged_at is not None
        assert not Path(entry.trash_path).exists()
        page = client.get("/api/v1/trash").json()
        assert page["reclaimed_bytes"] == 80
        assert page["items"] == []
    finally:
        db.close()


def test_empty_trash_deletes_only_inside_the_trash(client, app):
    _enable(client)
    now = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
    data = Path(app.state.settings.data_dir)
    movies = data / "media" / "Films"
    db = app.state.session_factory()
    try:
        givre = _file(movies, "Givre/Givre.mkv", b"g" * 100)
        orage = _file(movies, "Orage/Orage.mkv", b"o" * 50)
        givre_media = _media(db, title="Givre")
        orage_media = _media(db, title="Orage")
        givre_item = _item(db, givre_media, givre, watched_at=now)
        orage_item = _item(db, orage_media, orage, watched_at=now)
        db.commit()
        pairs = ((givre_media.id, givre_item.id), (orage_media.id, orage_item.id))
    finally:
        db.close()

    for media_id, item_id in pairs:
        removed = client.post(
            "/api/v1/cleanup/files",
            json={"media_id": media_id, "scope": "episode", "item_id": item_id, "confirm": True},
            headers=csrf(client),
        )
        assert removed.status_code == 200, removed.text

    listed = client.get("/api/v1/trash")
    assert listed.status_code == 200
    items = listed.json()["items"]
    assert len(items) == 2
    assert listed.json()["trash_bytes"] == 150
    outside = _file(data / "secret", "hors-corbeille.mkv", b"x" * 40)
    db = app.state.session_factory()
    try:
        rogue = TrashEntry(
            original_path=str(outside),
            trash_path=str(outside.resolve()),
            size=40,
            label="Hors corbeille",
            reason="manual",
            deleted_at=now,
            purge_after=now + timedelta(days=7),
        )
        db.add(rogue)
        db.commit()
        rogue_id = rogue.id
        trash_copy = Path(db.get(TrashEntry, items[0]["id"]).trash_path)
    finally:
        db.close()
    assert trash_copy.is_file()

    refused = client.post("/api/v1/trash/empty", json={"confirm": False}, headers=csrf(client))
    assert refused.status_code == 422
    assert trash_copy.is_file() and outside.is_file()

    one = client.delete(f"/api/v1/trash/{items[0]['id']}", headers=csrf(client))
    assert one.status_code == 200, one.text
    assert not trash_copy.is_file()
    assert outside.is_file()
    db = app.state.session_factory()
    try:
        closed = db.get(TrashEntry, items[0]["id"])
        assert closed.purged_at is not None
        single = db.scalar(select(Event).where(Event.type == "trash.purged"))
        assert single is not None
        assert single.payload["count"] == 1
        assert single.payload["bytes"] == items[0]["size"]
        other = Path(db.get(TrashEntry, items[1]["id"]).trash_path)
    finally:
        db.close()
    assert other.is_file()

    emptied = client.post("/api/v1/trash/empty", json={"confirm": True}, headers=csrf(client))
    assert emptied.status_code == 200, emptied.text
    body = emptied.json()
    assert body["purged"] == 2
    assert body["bytes"] == items[1]["size"]
    assert body["size_label"]
    assert not other.is_file()
    assert outside.is_file()
    page = client.get("/api/v1/trash").json()
    assert page["items"] == []
    db = app.state.session_factory()
    try:
        rogue_row = db.get(TrashEntry, rogue_id)
        assert rogue_row.purged_at is not None
        kept = db.get(TrashEntry, items[1]["id"])
        assert kept.purged_at is not None
        event = db.scalar(select(Event).where(Event.type == "trash.emptied"))
        assert event is not None
        assert event.payload["count"] == 2
        assert event.payload["bytes"] == items[1]["size"]
        assert "Corbeille vidée" in event.payload["message"]
    finally:
        db.close()


def test_path_safety_refuses_outside_roots(client, app):
    _enable(client)
    data = Path(app.state.settings.data_dir)
    movies = data / "media" / "Films"
    outside = _file(data / "secret", "notes.mkv", b"secret")
    link_target = _file(data / "secret", "linked.mkv", b"linked")
    movies.mkdir(parents=True)
    alias = movies / "alias.mkv"
    alias.symlink_to(link_target)
    db = app.state.session_factory()
    try:
        media = _media(db, title="Dehors")
        outside_item = _item(db, media, outside, watched_at=datetime.now(timezone.utc))
        link_media = _media(db, title="Lien")
        link_item = _item(db, link_media, alias, watched_at=datetime.now(timezone.utc))
        root_media = _media(db, title="Racine")
        root_item = MediaItem(
            media_id=root_media.id,
            title="Racine",
            state="downloaded",
            downloaded=True,
            watched=True,
            watched_at=datetime.now(timezone.utc),
            file_path=str(movies),
        )
        db.add(root_item)
        db.commit()
        outside_id = outside_item.id
        link_id = link_item.id
        root_id = root_item.id
        media_id = media.id
        link_media_id = link_media.id
        root_media_id = root_media.id
    finally:
        db.close()

    for payload in (
        {"media_id": media_id, "scope": "episode", "item_id": outside_id, "confirm": True},
        {"media_id": link_media_id, "scope": "episode", "item_id": link_id, "confirm": True},
        {"media_id": root_media_id, "scope": "episode", "item_id": root_id, "confirm": True},
    ):
        response = client.post("/api/v1/cleanup/files", json=payload, headers=csrf(client))
        assert response.status_code == 409, response.text
    assert outside.read_bytes() == b"secret"
    assert link_target.read_bytes() == b"linked"
    assert movies.is_dir()


def test_preview_does_not_delete_and_watched_is_not_requeued(client, app):
    _enable(client)
    now = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
    data = Path(app.state.settings.data_dir)
    path = _file(data / "media" / "Films", "Vu/Vu.mkv", b"v" * 8)
    db = app.state.session_factory()
    try:
        media = _media(db, type="series", title="Vu", auto_delete=True, new_episode_policy="auto")
        item = _item(db, media, path, season=1, episode=1, watched_at=now - timedelta(days=30))
        db.commit()
        due, upcoming = preview(db, app.state.settings, now=now)
        assert path.is_file()
        assert any(row.plan.media.id == media.id for row in upcoming)
        assert due == []
        assert classify(db, app.state.settings, now=now)
        _apply(
            db,
            media,
            [item],
            [Slot(season=1, episode=1, item_id=item.id)],
            [
                LinkCandidate(
                    url="https://dl-protect.example/a",
                    kind="ddl_protected",
                    hoster="rapidgator",
                    external_id="ext-1",
                    season=1,
                    episode=1,
                )
            ],
            1,
            ["rapidgator"],
            now,
        )
        assert db.scalars(select(Download)).all() == []
        item.watched = False
        item.downloaded = False
        item.source_available = False
        media.new_episode_policy = "propose"
        queued, proposed, _count = _apply(
            db,
            media,
            [item],
            [Slot(season=1, episode=1, item_id=item.id)],
            [
                LinkCandidate(
                    url="https://dl-protect.example/b",
                    kind="ddl_protected",
                    hoster="rapidgator",
                    external_id="ext-2",
                    season=1,
                    episode=1,
                )
            ],
            1,
            ["rapidgator"],
            now,
        )
        assert queued == 0
        assert proposed == 1
        fetched = TmdbTitle(
            tmdb_id=7,
            media_type="series",
            title="Vu",
            year=2020,
            overview=None,
            poster_url=None,
            series_status="returning",
            release_date=None,
            seasons=(
                TmdbSeason(
                    number=2,
                    name="Saison 2",
                    overview=None,
                    air_date=None,
                    episode_count=1,
                    tmdb_id=20,
                    episodes=(
                        TmdbEpisode(season=2, episode=1, title="Suite", air_date=None, tmdb_episode_id=21),
                    ),
                ),
            ),
        )
        announced = apply_new_seasons(db, media, fetched, now)
        assert announced == [2]
        assert apply_new_seasons(db, media, fetched, now) == []
    finally:
        db.close()


def test_manual_season_delete_keeps_tracking(client, app):
    _enable(client)
    now = datetime(2026, 10, 11, 8, tzinfo=timezone.utc)
    data = Path(app.state.settings.data_dir)
    series = data / "media" / "Series"
    one = _file(series, "Alpha/Season 01/a.mkv", b"a" * 4)
    two = _file(series, "Alpha/Season 02/b.mkv", b"b" * 4)
    db = app.state.session_factory()
    try:
        media = _media(db, type="series", title="Alpha")
        _item(db, media, one, season=1, episode=1, watched_at=now)
        _item(db, media, two, season=2, episode=1, watched_at=now)
        db.commit()
        media_id = media.id
    finally:
        db.close()
    kept = client.patch(f"/api/v1/media/{media_id}/seasons/2", json={"keep": True}, headers=csrf(client))
    assert kept.status_code == 200, kept.text
    assert kept.json()["keep"] is True
    removed = client.post(
        "/api/v1/cleanup/files",
        json={"media_id": media_id, "scope": "season", "season": 1, "confirm": True},
        headers=csrf(client),
    )
    assert removed.status_code == 200, removed.text
    assert not one.is_file()
    assert two.is_file()
    fiche = client.get(f"/api/v1/media/{media_id}")
    assert fiche.status_code == 200
    assert fiche.json()["title"] == "Alpha"


def _watch(item_id: str, season: int, episode: int, *, played: bool) -> JellyfinWatch:
    return JellyfinWatch(
        item_id=item_id,
        item_type="Episode",
        tmdb_id=None,
        season=season,
        episode=episode,
        path=None,
        played=played,
    )


def test_nimbus_delay_follows_latest_watch(client, app):
    """Série fictive : le délai part du dernier visionnage, un trou bloque la saison."""
    _enable(client)
    now = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
    series = Path(app.state.settings.data_dir) / "media" / "Series"
    db = app.state.session_factory()
    try:
        show = _media(db, type="series", title="Nimbus", auto_delete=True)
        early = _file(series, "Nimbus/Season 01/e1.mkv", b"a" * 8)
        late = _file(series, "Nimbus/Season 01/e2.mkv", b"b" * 8)
        old_a = _file(series, "Nimbus/Season 02/e1.mkv", b"c" * 8)
        old_b = _file(series, "Nimbus/Season 02/e2.mkv", b"d" * 8)
        gap_a = _file(series, "Nimbus/Season 03/e1.mkv", b"e" * 8)
        gap_b = _file(series, "Nimbus/Season 03/e2.mkv", b"f" * 8)
        blank_a = _file(series, "Nimbus/Season 04/e1.mkv", b"g" * 8)
        blank_b = _file(series, "Nimbus/Season 04/e2.mkv", b"h" * 8)
        s1e1 = _item(db, show, early, season=1, episode=1, watched_at=now - timedelta(days=40))
        s1e2 = _item(db, show, late, season=1, episode=2, watched_at=now - timedelta(days=2))
        s2e1 = _item(db, show, old_a, season=2, episode=1, watched_at=now - timedelta(days=30))
        _item(db, show, old_b, season=2, episode=2, watched_at=now - timedelta(days=20))
        _item(db, show, gap_a, season=3, episode=1, watched_at=now - timedelta(days=40))
        _item(db, show, gap_b, season=3, episode=2, watched=False)
        _item(db, show, blank_a, season=4, episode=1, watched_at=now - timedelta(days=40))
        undated = _item(db, show, blank_b, season=4, episode=2, watched_at=now - timedelta(days=40))
        undated.watched = True
        undated.watched_at = None
        db.commit()

        def seasons(moment: datetime) -> set[int | None]:
            return {row.plan.season for row in classify(db, app.state.settings, moment)}

        assert seasons(now) == {2}
        plan = next(row.plan for row in classify(db, app.state.settings, now) if row.plan.season == 2)
        assert plan.eligible_at == (now - timedelta(days=20)) + timedelta(days=14)

        s1e2.watched_at = now - timedelta(days=20)
        db.commit()
        assert seasons(now) == {1, 2}

        s2e1.watched = False
        s2e1.watched_at = None
        db.commit()
        assert 2 not in seasons(now)

        s2e1.watched = True
        s2e1.watched_at = now
        db.commit()
        assert 2 not in seasons(now)

        previous = s1e1.watched_at
        apply_played(db, s1e1, _watch("jf-nimbus-s1e1", 1, 1, played=True), now + timedelta(days=3))
        assert s1e1.watched_at == previous

        apply_played(db, s2e1, _watch("jf-nimbus-s2e1", 2, 1, played=False), now)
        assert s2e1.watched is False and s2e1.watched_at is None
        sync_at = now + timedelta(hours=1)
        apply_played(db, s2e1, _watch("jf-nimbus-s2e1", 2, 1, played=True), sync_at)
        assert s2e1.watched_at == sync_at
        db.commit()
        assert 2 not in seasons(now)

        maintenance(db, app.state.settings, now=now)
        assert early.is_file() and late.is_file()
        maintenance(db, app.state.settings, now=now + timedelta(hours=25))
        assert not early.is_file() and not late.is_file()
        assert s1e1.watched is True
        assert s1e1.watched_at == now - timedelta(days=40)
        assert s1e1.downloaded is False and s1e1.file_path is None

        early.parent.mkdir(parents=True, exist_ok=True)
        early.write_bytes(b"a" * 8)
        late.write_bytes(b"b" * 8)
        reappeared = now + timedelta(hours=26)
        s1e1.downloaded = True
        s1e1.file_path = str(early)
        s1e1.downloaded_at = reappeared
        s1e2.downloaded = True
        s1e2.file_path = str(late)
        s1e2.downloaded_at = reappeared
        db.commit()
        again = [row for row in classify(db, app.state.settings, reappeared) if row.plan.season == 1]
        assert len(again) == 1
        assert again[0].phase == "upcoming"
        assert again[0].plan.eligible_at == (now - timedelta(days=20)) + timedelta(days=14)
        maintenance(db, app.state.settings, now=reappeared + timedelta(hours=1))
        notice = db.scalar(
            select(Event).where(
                Event.type == "cleanup.upcoming",
                Event.dedupe_key == f"cleanup.upcoming:media:{show.id}:season:1",
            )
        )
        assert notice is not None
        from mediatracker.core.cleanup import _aware

        assert _aware(notice.ts) >= reappeared
        maintenance(db, app.state.settings, now=reappeared + timedelta(hours=26))
        assert not early.is_file() and not late.is_file()
    finally:
        db.close()


def test_watched_film_stays_until_the_fiche_allows_cleanup(client, app):
    """A film watched for a month stays when the fiche switch is off, and says why."""
    _enable(client)
    now = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
    movies = Path(app.state.settings.data_dir) / "media" / "Films"
    db = app.state.session_factory()
    try:
        kept = _file(movies, "Latence/Latence.mkv", b"film" * 4)
        old = _file(movies, "Ancien/Ancien.mkv", b"old" * 4)
        blank = _file(movies, "SansDate/SansDate.mkv", b"nodate")
        outside = Path(app.state.settings.data_dir) / "dehors.mkv"
        outside.write_bytes(b"out")
        waiting = _media(db, title="Latence", keep=True)
        ready = _media(db, title="Ancien")
        undated = _media(db, title="SansDate", auto_delete=True)
        stray = _media(db, title="Dehors", auto_delete=True)
        _item(db, waiting, kept, watched_at=now - timedelta(days=40))
        _item(db, ready, old, watched_at=now - timedelta(days=40))
        missing_date = _item(db, undated, blank, watched_at=now - timedelta(days=40))
        missing_date.watched = True
        missing_date.watched_at = None
        _item(db, stray, outside, watched_at=now - timedelta(days=40))
        db.commit()

        def reason(media):
            items = list(db.scalars(select(MediaItem).where(MediaItem.media_id == media.id)).all())
            return explain_film(db, app.state.settings, media, items, now)

        held = reason(waiting)
        assert held is not None and held.code == "keep"
        assert "Garder" in held.message
        assert "7 sept. 2026" in held.message
        assert all(row.plan.media.id != waiting.id for row in classify(db, app.state.settings, now))

        soon = reason(ready)
        assert soon is not None and soon.code == "upcoming"
        assert "dès l'envoi de l'avis + 24 h" in soon.message
        assert any(row.plan.media.id == ready.id for row in classify(db, app.state.settings, now))

        no_date = reason(undated)
        assert no_date is not None and no_date.code == "no_date"
        assert all(row.plan.media.id != undated.id for row in classify(db, app.state.settings, now))

        lost = reason(stray)
        assert lost is not None and lost.code == "outside"

        maintenance(db, app.state.settings, now=now)
        passes = list(db.scalars(select(Event).where(Event.type == "cleanup.pass")).all())
        assert len(passes) == 1
        assert "Latence" in passes[0].payload["message"]
        assert passes[0].payload["films"]
        maintenance(db, app.state.settings, now=now + timedelta(minutes=30))
        assert db.scalar(select(Event).where(Event.type == "cleanup.pass"))
        assert len(list(db.scalars(select(Event).where(Event.type == "cleanup.pass")).all())) == 1
        maintenance(db, app.state.settings, now=now + timedelta(hours=2))
        assert len(list(db.scalars(select(Event).where(Event.type == "cleanup.pass")).all())) == 2
        waiting_id = waiting.id
    finally:
        db.close()

    fiche = client.get(f"/api/v1/media/{waiting_id}")
    assert fiche.status_code == 200, fiche.text
    assert fiche.json()["cleanup_code"] == "keep"
    assert "Garder" in fiche.json()["cleanup_reason"]
    listed = client.get("/api/v1/media")
    assert listed.status_code == 200
    row = next(item for item in listed.json()["items"] if item["id"] == waiting_id)
    assert row["cleanup_reason"] == fiche.json()["cleanup_reason"]


def test_brume_season_stays_until_every_episode_is_watched(client, app):
    _enable(client)
    now = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
    series = Path(app.state.settings.data_dir) / "media" / "Series"
    db = app.state.session_factory()
    try:
        show = _media(db, type="series", title="Brume", auto_delete=True)
        first = _file(series, "Brume/Season 01/e1.mkv", b"a" * 8)
        second = _file(series, "Brume/Season 01/e2.mkv", b"b" * 8)
        done = _file(series, "Brume/Season 02/e1.mkv", b"c" * 8)
        _item(db, show, first, season=1, episode=1, watched_at=now - timedelta(days=40))
        _item(db, show, second, season=1, episode=2, watched=False)
        _item(db, show, done, season=2, episode=1, watched_at=now - timedelta(days=40))
        db.commit()
        items = list(db.scalars(select(MediaItem).where(MediaItem.media_id == show.id)).all())
        reason = explain_media(db, app.state.settings, show, items, now)
        assert reason is not None
        assert "Saison 1 : pas entièrement vue." in reason.message
        assert "Saison 2 : préavis" in reason.message
        assert {row.plan.season for row in classify(db, app.state.settings, now)} == {2}
        assert first.is_file() and second.is_file() and done.is_file()
        show_id = show.id
    finally:
        db.close()
    fiche = client.get(f"/api/v1/media/{show_id}")
    assert fiche.status_code == 200, fiche.text
    assert "pas entièrement vue" in fiche.json()["cleanup_reason"]


def test_one_volume_failure_does_not_stop_the_next_film(client, app, monkeypatch):
    _enable(client)
    now = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
    movies = Path(app.state.settings.data_dir) / "media" / "Films"
    db = app.state.session_factory()
    try:
        brume = _file(movies, "Brume/Brume.mkv", b"b" * 8)
        cendre = _file(movies, "Cendre/Cendre.mkv", b"c" * 8)
        first = _media(db, title="Brume", auto_delete=True)
        second = _media(db, title="Cendre", auto_delete=True)
        _item(db, first, brume, watched_at=now - timedelta(days=40))
        _item(db, second, cendre, watched_at=now - timedelta(days=40))
        db.commit()
        maintenance(db, app.state.settings, now=now)
        assert brume.is_file() and cendre.is_file()

        def flaky(src, dest):
            if src.name.startswith("Brume"):
                raise OSError("CROSS_DEVICE")
            atomic_move(src, dest)

        monkeypatch.setattr("mediatracker.core.cleanup.atomic_move", flaky)
        maintenance(db, app.state.settings, now=now + timedelta(hours=25))
        assert brume.is_file()
        assert not cendre.is_file()
        failed = list(db.scalars(select(Event).where(Event.type == "cleanup.failed")).all())
        assert len(failed) == 1
        assert failed[0].payload["title"] == "Brume"
        assert "volume" in failed[0].payload["message"]
        held = list(db.scalars(select(TrashEntry)).all())
        assert any("Cendre" in row.original_path for row in held)
        assert all("Brume" not in row.original_path for row in held)
        cendre_item = db.scalar(select(MediaItem).where(MediaItem.media_id == second.id))
        assert cendre_item.downloaded is False and cendre_item.file_path is None
        assert cendre_item.watched is True
    finally:
        db.close()


def test_upcoming_cleanup_message_uses_notice_delay(client, app):
    """Trash date shown to the user respects max(eligible, notice sent + delay)."""
    _enable(client)
    now = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
    movies = Path(app.state.settings.data_dir) / "media" / "Films"
    db = app.state.session_factory()
    try:
        path = _file(movies, "Horizon/Horizon.mkv", b"h" * 8)
        media = _media(db, title="Horizon")
        watched_at = now - timedelta(days=40)
        _item(db, media, path, watched_at=watched_at)
        db.commit()

        items = list(db.scalars(select(MediaItem).where(MediaItem.media_id == media.id)).all())
        before = explain_film(db, app.state.settings, media, items, now)
        assert before is not None and before.code == "upcoming"
        assert "dès l'envoi de l'avis + 24 h" in before.message

        notice_at = now - timedelta(hours=2)
        db.add(
            Event(
                type="cleanup.upcoming",
                dedupe_key=f"cleanup.upcoming:media:{media.id}",
                ts=notice_at,
                level="info",
                payload={"title": media.title},
            )
        )
        db.commit()

        after = explain_film(db, app.state.settings, media, items, now)
        assert after is not None and after.code == "upcoming"
        assert "dès l'envoi de l'avis" not in after.message
        assert "24 oct. 2026" in after.message
        assert after.eligible_at == notice_at + timedelta(hours=24)
    finally:
        db.close()
