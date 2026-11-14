"""ALREADY_LOCAL requires a video file, not a sidecar, a stale row, or the trash."""

from datetime import datetime, timedelta, timezone

from tests.conftest import csrf, login
from tests.test_pipeline import FakeDownloader

from mediatracker.core.pipeline import tick
from mediatracker.core.storage import incomplete_dir, storage_layout
from mediatracker.db.models import Download, LibraryFile, Media, MediaItem

NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)


def _citadelle_demo(client) -> tuple[int, int]:
    created = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Citadelle Démo", "year": 2023, "tmdb_id": 900120},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    media_id = created.json()["id"]
    episode = client.post(
        f"/api/v1/media/{media_id}/items",
        json={"season": 1, "episode": 2, "title": "Épisode pilote démo"},
        headers=csrf(client),
    )
    assert episode.status_code == 201, episode.text
    return media_id, episode.json()["id"]


def _layout(app):
    db = app.state.session_factory()
    try:
        return storage_layout(db, app.state.settings)
    finally:
        db.close()


def _season(app):
    layout = _layout(app)
    folder = layout.series_dir / "Citadelle Démo (2023) [tmdbid-900120]" / "Season 01"
    folder.mkdir(parents=True)
    return layout, folder


def _gone_episode(app, folder, item_id: int):
    """The mkv was deleted by hand. Sidecars, trash and incomplete copies remain."""
    del item_id
    video = folder / "Citadelle Démo (2023) - S01E02.mkv"
    video.write_bytes(b"episode")
    (folder / "Citadelle Démo (2023) - S01E02.nfo").write_bytes(b"<episode/>")
    (folder / "Citadelle Démo (2023) - S01E02-thumb.jpg").write_bytes(b"jpg")
    (folder / "Citadelle Démo (2023) - S01E01.mkv").write_bytes(b"one")
    (folder / "Citadelle Démo (2023) - S01E03.mkv").write_bytes(b"three")
    layout = _layout(app)
    incomplete = incomplete_dir(layout)
    incomplete.mkdir(parents=True, exist_ok=True)
    (incomplete / "Citadelle Démo.S01E02.mkv").write_bytes(b"partial")
    trashed = layout.trash_dir / "20261016T120000"
    trashed.mkdir(parents=True, exist_ok=True)
    (trashed / "Citadelle Démo (2023) - S01E02.mkv").write_bytes(b"old-copy")
    video.unlink()
    return video


def test_deleted_mkv_is_not_already_local_when_only_sidecars_remain(client, app):
    login(client)
    _media_id, item_id = _citadelle_demo(client)
    _layout, folder = _season(app)
    video = _gone_episode(app, folder, item_id)
    db = app.state.session_factory()
    try:
        item = db.get(MediaItem, item_id)
        item.watched = True
        item.downloaded = True
        item.file_path = str(video)
        item.state = "downloaded"
        db.add(
            LibraryFile(
                path=str(video),
                size=7,
                parsed_title="Citadelle Démo",
                parsed_year=2023,
                season=1,
                episode=2,
                tmdb_id=900120,
                media_type="series",
                media_id=item.media_id,
                media_item_id=item.id,
                status="matched",
                confidence="high",
                seen_at=NOW,
            )
        )
        db.add(Download(media_item_id=item.id, state="done", created_at=NOW - timedelta(days=30)))
        pending = Download(media_item_id=item.id, state="pending", created_at=NOW)
        db.add(pending)
        db.commit()
        pending_id = pending.id
        tick(db, app.state.settings, now=NOW, downloader=FakeDownloader())
        row = db.get(Download, pending_id)
        item = db.get(MediaItem, item_id)
        assert row.error_code != "ALREADY_LOCAL"
        assert row.state != "cancelled"
        assert item.downloaded is False
        assert item.file_path is None
        assert item.watched is True
        stored = db.query(LibraryFile).filter(LibraryFile.path == str(video)).one()
        assert stored.status == "missing"
    finally:
        db.close()


def test_retry_rechecks_the_disk_and_queues_when_the_video_is_gone(client, app):
    login(client)
    _media_id, item_id = _citadelle_demo(client)
    _layout, folder = _season(app)
    video = _gone_episode(app, folder, item_id)
    db = app.state.session_factory()
    try:
        item = db.get(MediaItem, item_id)
        item.downloaded = True
        item.file_path = str(video)
        item.state = "downloaded"
        db.add(
            LibraryFile(
                path=str(video),
                size=7,
                parsed_title="Citadelle Démo",
                parsed_year=2023,
                season=1,
                episode=2,
                media_type="series",
                media_id=item.media_id,
                media_item_id=item.id,
                status="matched",
                confidence="high",
                seen_at=NOW,
            )
        )
        db.add(Download(media_item_id=item.id, state="done", created_at=NOW - timedelta(days=30)))
        cancelled = Download(
            media_item_id=item.id,
            state="cancelled",
            error_code="ALREADY_LOCAL",
            error="Déjà présent dans la bibliothèque.",
            created_at=NOW - timedelta(hours=1),
            finished_at=NOW - timedelta(hours=1),
        )
        db.add(cancelled)
        db.commit()
        cancelled_id = cancelled.id
    finally:
        db.close()

    retried = client.post(f"/api/v1/downloads/{cancelled_id}/retry", headers=csrf(client))
    assert retried.status_code == 200, retried.text
    body = retried.json()
    assert body["state"] == "pending"
    assert body["error_code"] is None
    db = app.state.session_factory()
    try:
        item = db.get(MediaItem, item_id)
        assert item.downloaded is False
        assert item.file_path is None
        stored = db.query(LibraryFile).filter(LibraryFile.path == str(video)).one()
        assert stored.status == "missing"
        assert stored.missing_at is not None
        tick(db, app.state.settings, now=NOW, downloader=FakeDownloader())
        row = db.get(Download, cancelled_id)
        assert row.error_code != "ALREADY_LOCAL"
        assert row.state != "done"
    finally:
        db.close()


def test_retry_all_does_not_treat_a_deleted_file_as_obtained(client, app):
    login(client)
    _media_id, item_id = _citadelle_demo(client)
    _layout, folder = _season(app)
    video = _gone_episode(app, folder, item_id)
    db = app.state.session_factory()
    try:
        item = db.get(MediaItem, item_id)
        item.downloaded = True
        item.file_path = str(video)
        item.state = "downloaded"
        db.add(Download(media_item_id=item.id, state="done", created_at=NOW - timedelta(days=20)))
        cancelled = Download(
            media_item_id=item.id,
            state="cancelled",
            error_code="ALREADY_LOCAL",
            error="Déjà présent dans la bibliothèque.",
            created_at=NOW,
        )
        db.add(cancelled)
        db.commit()
        cancelled_id = cancelled.id
    finally:
        db.close()

    again = client.post(f"/api/v1/downloads/{cancelled_id}/retry-all", headers=csrf(client))
    assert again.status_code == 200, again.text
    assert again.json()["state"] == "pending"
    assert again.json()["error_code"] == "REDISCOVER"
    db = app.state.session_factory()
    try:
        item = db.get(MediaItem, item_id)
        assert item.downloaded is False
        media = db.get(Media, item.media_id)
        assert media.title == "Citadelle Démo"
    finally:
        db.close()


def test_retry_stays_cancelled_when_the_video_is_still_there(client, app):
    login(client)
    _media_id, item_id = _citadelle_demo(client)
    _layout, folder = _season(app)
    video = folder / "Citadelle Démo (2023) - S01E02.mkv"
    video.write_bytes(b"still-there")
    (folder / "Citadelle Démo (2023) - S01E02.nfo").write_bytes(b"<episode/>")
    db = app.state.session_factory()
    try:
        item = db.get(MediaItem, item_id)
        item.downloaded = True
        item.file_path = str(video)
        item.state = "downloaded"
        cancelled = Download(
            media_item_id=item.id,
            state="cancelled",
            error_code="ALREADY_LOCAL",
            error="Déjà présent dans la bibliothèque.",
            created_at=NOW,
        )
        db.add(cancelled)
        db.commit()
        cancelled_id = cancelled.id
    finally:
        db.close()

    retried = client.post(f"/api/v1/downloads/{cancelled_id}/retry", headers=csrf(client))
    assert retried.status_code == 200, retried.text
    assert retried.json()["state"] == "cancelled"
    assert retried.json()["error_code"] == "ALREADY_LOCAL"
    assert "déjà présent" in retried.json()["error"].casefold()


def _claim_deleted(app, item_id: int, video, *, watched: bool) -> None:
    db = app.state.session_factory()
    try:
        item = db.get(MediaItem, item_id)
        item.watched = watched
        item.downloaded = True
        item.file_path = str(video)
        item.state = "downloaded"
        db.add(
            LibraryFile(
                path=str(video),
                size=7,
                parsed_title="Citadelle Démo",
                parsed_year=2023,
                season=1,
                episode=2,
                tmdb_id=900120,
                media_type="series",
                media_id=item.media_id,
                media_item_id=item.id,
                status="matched",
                confidence="high",
                seen_at=NOW,
            )
        )
        db.add(Download(media_item_id=item.id, state="done", created_at=NOW - timedelta(days=30)))
        db.commit()
    finally:
        db.close()


def test_fiche_clears_a_deleted_video_without_queueing_it(client, app):
    """Opening the fiche resyncs the row. A watched episode is not downloaded again."""
    from tests.conftest import connect_alldebrid_for_tests

    login(client)
    connect_alldebrid_for_tests(app)
    media_id, item_id = _citadelle_demo(client)
    _layout, folder = _season(app)
    video = _gone_episode(app, folder, item_id)
    _claim_deleted(app, item_id, video, watched=True)

    listed = client.get(f"/api/v1/media/{media_id}/items")
    assert listed.status_code == 200, listed.text
    episode = listed.json()["seasons"][0]["episodes"][0]
    assert episode["downloaded"] is False
    assert episode["filename"] is None
    assert episode["watched"] is True
    assert episode["state"] == "missing"

    db = app.state.session_factory()
    try:
        from mediatracker.core.source_poll import _blocking_download

        assert db.query(Download).filter(Download.media_item_id == item_id).count() == 1
        assert db.query(Download).filter(Download.state == "pending").count() == 0
        stored = db.query(LibraryFile).filter(LibraryFile.media_item_id == item_id).one()
        assert stored.status == "missing"
        assert stored.missing_at is not None
        assert _blocking_download(db, item_id) is False
        item = db.get(MediaItem, item_id)
        assert item.watched is True
    finally:
        db.close()

    forced = client.post(
        f"/api/v1/media/{media_id}/force-download",
        json={"item_id": item_id},
        headers=csrf(client),
    )
    assert forced.status_code == 200, forced.text
    body = forced.json()
    assert body["queued"] == 1
    assert body["needs_confirm"] is False
    assert "S01E02" in body["message"]
    db = app.state.session_factory()
    try:
        item = db.get(MediaItem, item_id)
        assert item.watched is True
        assert item.downloaded is False
        assert item.file_path is None
        assert db.query(Download).filter(Download.media_item_id == item_id, Download.state == "pending").count() == 1
    finally:
        db.close()


def test_fiche_keeps_downloaded_while_the_video_exists(client, app):
    login(client)
    media_id, item_id = _citadelle_demo(client)
    _layout, folder = _season(app)
    video = folder / "Citadelle Démo S01E02.mkv"
    video.write_bytes(b"still-there")
    (folder / "Citadelle Démo S01E02.nfo").write_bytes(b"<episode/>")
    db = app.state.session_factory()
    try:
        item = db.get(MediaItem, item_id)
        item.downloaded = True
        item.file_path = str(video)
        item.state = "downloaded"
        item.watched = False
        db.commit()
    finally:
        db.close()

    episode = client.get(f"/api/v1/media/{media_id}/items").json()["seasons"][0]["episodes"][0]
    assert episode["downloaded"] is True
    assert episode["filename"] == "Citadelle Démo S01E02.mkv"
    assert episode["watched"] is False


def test_opening_the_fiche_does_not_queue_an_unwatched_gap(client, app):
    """The check queues a past unwatched gap. Displaying the fiche does not."""
    login(client)
    media_id, item_id = _citadelle_demo(client)
    _layout, folder = _season(app)
    video = _gone_episode(app, folder, item_id)
    _claim_deleted(app, item_id, video, watched=False)

    episode = client.get(f"/api/v1/media/{media_id}/items").json()["seasons"][0]["episodes"][0]
    assert episode["downloaded"] is False
    assert episode["filename"] is None
    assert episode["watched"] is False
    db = app.state.session_factory()
    try:
        from mediatracker.core.source_poll import _blocking_download

        assert db.query(Download).filter(Download.state == "pending").count() == 0
        assert _blocking_download(db, item_id) is False
    finally:
        db.close()
