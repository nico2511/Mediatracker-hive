"""A video already in the library is not a failed download, and Home Assistant hears it once."""

from datetime import datetime, timezone
from pathlib import Path

from tests.conftest import csrf, login
from tests.test_pipeline import FakeDebrid, FakeDownloader

from mediatracker.core.library_video import better_video, find_library_video
from mediatracker.core.pipeline import tick
from mediatracker.db.models import (
    Download,
    Event,
    LibraryFile,
    Link,
    Media,
    MediaItem,
    Provider,
    TrashEntry,
    WebhookDelivery,
)
from mediatracker.events import record_event
from mediatracker.events.deliver import deliver_due

NOW = datetime(2026, 10, 13, tzinfo=timezone.utc)


def test_library_video_matches_canonical_names_only(tmp_path):
    series = tmp_path / "Series"
    movies = tmp_path / "Films"
    tagged = series / "Détective Démo (2025) [tmdbid-2511]" / "Season 01"
    plain = series / "Détective Démo (2025)" / "Season 1"
    tagged.mkdir(parents=True)
    plain.mkdir(parents=True)
    (tagged / "Détective Démo (2025) - S01E11.mkv").write_bytes(b"other")
    (tagged / "Détective Démo (2025) - S01E12.sample.mkv").write_bytes(b"sample-bytes")
    (tagged / "Détective Démo (2025) - S01E12.mkv").write_bytes(b"")
    kept = plain / "Détective Démo (2025) - S01E12.mp4"
    kept.write_bytes(b"episode")

    found = find_library_video(
        movies_dir=movies,
        series_dir=series,
        media_type="series",
        title="Détective Démo",
        year=2025,
        season=1,
        episode=12,
        tmdb_id=2511,
    )
    assert found == kept
    assert (
        find_library_video(
            movies_dir=movies,
            series_dir=series,
            media_type="series",
            title="Détective Démo",
            year=2025,
            season=1,
            episode=3,
            tmdb_id=2511,
        )
        is None
    )
    low = tmp_path / "low.mkv"
    high = tmp_path / "high.mkv"
    low.write_bytes(b"x" * 50)
    high.write_bytes(b"y" * 10)
    assert better_video(high, low, challenger_qualities=("1080p",), incumbent_qualities=("720p",))
    assert not better_video(low, high, challenger_qualities=("720p",), incumbent_qualities=("1080p",))


def _detective_demo(client) -> tuple[int, int]:
    created = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Détective Démo", "year": 2025},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    media_id = created.json()["id"]
    episode = client.post(
        f"/api/v1/media/{media_id}/items",
        json={"season": 1, "episode": 12, "title": "Épisode 12"},
        headers=csrf(client),
    )
    assert episode.status_code == 201, episode.text
    return media_id, episode.json()["id"]


def _tick(db, app, downloader):
    tick(db, app.state.settings, now=NOW, debrid=FakeDebrid(), downloader=downloader)


def test_download_does_not_start_when_the_episode_is_already_on_disk(client, app):
    login(client)
    media_id, item_id = _detective_demo(client)
    series = Path(app.state.settings.data_dir) / "media" / "Series"
    folder = series / "Détective Démo (2025)" / "Season 01"
    folder.mkdir(parents=True)
    video = folder / "Détective Démo (2025) - S01E12.mp4"
    video.write_bytes(b"already-there")
    db = app.state.session_factory()
    downloader = FakeDownloader()
    try:
        media = db.get(Media, media_id)
        media.tmdb_id = 2511
        db.add(Download(media_item_id=item_id, state="pending", created_at=NOW))
        _tick(db, app, downloader)
        item = db.get(MediaItem, item_id)
        row = db.query(Download).one()
        assert item.downloaded is True
        assert item.file_path == str(video)
        assert row.state == "cancelled"
        assert row.error_code == "ALREADY_LOCAL"
        assert db.query(Event).filter(Event.type == "download.failed").count() == 0
    finally:
        db.close()
    assert downloader.urls == []


def test_download_cancels_when_a_release_name_already_holds_the_episode(client, app):
    login(client)
    created = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Lanternes Démo", "year": 2026},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    media_id = created.json()["id"]
    episode = client.post(
        f"/api/v1/media/{media_id}/items",
        json={"season": 1, "episode": 8, "title": "Épisode 8"},
        headers=csrf(client),
    )
    assert episode.status_code == 201, episode.text
    item_id = episode.json()["id"]
    series = Path(app.state.settings.data_dir) / "media" / "Series"
    folder = series / "Incoming"
    folder.mkdir(parents=True)
    video = folder / "Lanternes Démo.S01E08.FiNAL.1080p.WEB.H264-GL0P-Wawacity.skin.mkv"
    video.write_bytes(b"already-there")
    db = app.state.session_factory()
    downloader = FakeDownloader()
    try:
        db.add(Download(media_item_id=item_id, state="pending", created_at=NOW))
        _tick(db, app, downloader)
        item = db.get(MediaItem, item_id)
        row = db.query(Download).one()
        assert item.downloaded is True
        assert item.file_path == str(video)
        assert row.state == "cancelled"
        assert row.error_code == "ALREADY_LOCAL"
    finally:
        db.close()
    assert downloader.urls == []


def _file_download(db, item_id: int, source: Path, *, quality: str, external: str) -> Download:
    provider = db.query(Provider).filter(Provider.type == "wawacity").one()
    link = Link(
        media_item_id=item_id,
        provider_id=provider.id,
        kind="hoster",
        url=f"https://example.test/{external}",
        quality=quality,
        filename=f"Détective Démo.S01E12.{quality}.WEB.mkv",
        external_id=external,
        discovered_at=NOW,
    )
    db.add(link)
    db.flush()
    row = Download(
        media_item_id=item_id,
        link_id=link.id,
        state="post_processing",
        filename=source.name,
        error=str(source),
        created_at=NOW,
    )
    db.add(row)
    db.flush()
    return row


def test_filing_keeps_the_better_video_and_does_not_notify_a_failure(client, app):
    login(client)
    media_id, item_id = _detective_demo(client)
    series = Path(app.state.settings.data_dir) / "media" / "Series"
    season = series / "Détective Démo (2025) [tmdbid-2511]" / "Season 01"
    season.mkdir(parents=True)
    dest = season / "Détective Démo (2025) - S01E12.mkv"
    dest.write_bytes(b"o" * 1000)
    source = Path(app.state.settings.data_dir) / "downloads" / "incomplete" / "Détective Démo - S01E12.mkv"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"n" * 4000)
    db = app.state.session_factory()
    try:
        media = db.get(Media, media_id)
        media.tmdb_id = 2511
        db.add(
            LibraryFile(
                path=str(dest.resolve()),
                size=1000,
                parsed_title="Détective Démo",
                parsed_year=2025,
                season=1,
                episode=12,
                media_type="series",
                media_id=media_id,
                media_item_id=item_id,
                status="matched",
                original_name="Détective Démo.S01E12.720p.WEB.mkv",
                release_quality="720p",
                seen_at=NOW,
            )
        )
        row = _file_download(db, item_id, source, quality="1080p", external="better")
        _tick(db, app, FakeDownloader())
        db.refresh(row)
        item = db.get(MediaItem, item_id)
        assert row.state == "done"
        assert row.error_code is None
        assert item.downloaded is True
        assert item.state == "downloaded"
        assert dest.read_bytes() == b"n" * 4000
        assert not source.exists()
        trashed = db.query(TrashEntry).one()
        assert trashed.reason == "duplicate"
        assert Path(trashed.trash_path).read_bytes() == b"o" * 1000
        assert db.query(Event).filter(Event.type == "download.failed").count() == 0
        assert db.query(Event).filter(Event.type == "download.completed").count() == 1
    finally:
        db.close()


def test_filing_trashes_the_download_when_the_library_copy_is_better(client, app):
    login(client)
    media_id, item_id = _detective_demo(client)
    series = Path(app.state.settings.data_dir) / "media" / "Series"
    plain = series / "Détective Démo (2025)" / "Season 01"
    plain.mkdir(parents=True)
    existing = plain / "Détective Démo (2025) - S01E12.mkv"
    existing.write_bytes(b"k" * 2000)
    source = Path(app.state.settings.data_dir) / "downloads" / "incomplete" / "Détective Démo - S01E12.mkv"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"w" * 9000)
    db = app.state.session_factory()
    try:
        media = db.get(Media, media_id)
        media.tmdb_id = 2511
        db.add(
            LibraryFile(
                path=str(existing.resolve()),
                size=2000,
                parsed_title="Détective Démo",
                season=1,
                episode=12,
                media_type="series",
                media_id=media_id,
                media_item_id=item_id,
                status="matched",
                original_name="Détective Démo.S01E12.1080p.WEB.mkv",
                release_quality="1080p",
                seen_at=NOW,
            )
        )
        row = _file_download(db, item_id, source, quality="720p", external="worse")
        _tick(db, app, FakeDownloader())
        db.refresh(row)
        item = db.get(MediaItem, item_id)
        tagged = series / "Détective Démo (2025) [tmdbid-2511]" / "Season 01" / "Détective Démo (2025) - S01E12.mkv"
        assert row.state == "done"
        assert row.error_code is None
        assert item.downloaded is True
        assert item.file_path == str(existing)
        assert existing.read_bytes() == b"k" * 2000
        assert not tagged.exists()
        assert not source.exists()
        assert Path(db.query(TrashEntry).one().trash_path).read_bytes() == b"w" * 9000
        assert db.query(Event).filter(Event.type == "download.failed").count() == 0
    finally:
        db.close()


def test_empty_placeholder_still_fails_without_replacing_it(client, app):
    login(client)
    _media_id, item_id = _detective_demo(client)
    series = Path(app.state.settings.data_dir) / "media" / "Series"
    season = series / "Détective Démo (2025)" / "Season 01"
    season.mkdir(parents=True)
    dest = season / "Détective Démo (2025) - S01E12.mkv"
    dest.write_bytes(b"")
    source = Path(app.state.settings.data_dir) / "downloads" / "incomplete" / "Détective Démo - S01E12.mkv"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"real-video")
    db = app.state.session_factory()
    try:
        row = _file_download(db, item_id, source, quality="1080p", external="empty")
        _tick(db, app, FakeDownloader())
        db.refresh(row)
        assert row.state == "failed"
        assert row.error_code == "FILE_EXISTS"
        assert row.error == "Un fichier porte déjà ce nom dans la bibliothèque."
        assert dest.read_bytes() == b""
        assert source.read_bytes() == b"real-video"
        assert db.query(TrashEntry).count() == 0
    finally:
        db.close()


def test_home_assistant_notifies_the_same_failure_once(client, app, monkeypatch):
    login(client)
    saved = client.patch(
        "/api/v1/settings",
        json={"values": {"notify_group_seconds": 0}},
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text
    first = client.post(
        "/api/v1/webhooks",
        json={
            "name": "salon",
            "url": "http://homeassistant.local:8123/api/webhook/mediatracker",
            "type": "homeassistant",
            "events": ["download.failed"],
        },
        headers=csrf(client),
    )
    second = client.post(
        "/api/v1/webhooks",
        json={
            "name": "salon-bis",
            "url": "http://homeassistant.local:8123/api/webhook/mediatracker",
            "type": "homeassistant",
            "events": ["download.failed"],
        },
        headers=csrf(client),
    )
    other = client.post(
        "/api/v1/webhooks",
        json={
            "name": "telephone",
            "url": "http://homeassistant.local:8123/api/webhook/telephone",
            "type": "homeassistant",
            "events": ["download.failed"],
        },
        headers=csrf(client),
    )
    assert first.status_code == second.status_code == other.status_code == 201
    calls: list[str] = []

    def send(url, body, headers):
        del headers
        calls.append(url)
        return 200

    monkeypatch.setattr("mediatracker.events.deliver.post_request", send)
    media_id, item_id = _detective_demo(client)
    payload = {
        "title": "Détective Démo",
        "media_id": media_id,
        "item_id": item_id,
        "season": 1,
        "episode": 12,
        "error_code": "FILE_EXISTS",
        "error": "Un fichier porte déjà ce nom dans la bibliothèque.",
        "level": "warning",
    }
    db = app.state.session_factory()
    try:
        record_event(
            db,
            "download.failed",
            level="warning",
            media_id=media_id,
            item_id=item_id,
            dedupe_key="download.failed:1",
            when=NOW,
            payload=payload,
        )
        record_event(
            db,
            "download.failed",
            level="warning",
            media_id=media_id,
            item_id=item_id,
            dedupe_key="download.failed:2",
            when=NOW,
            payload=payload,
        )
        db.commit()
        assert deliver_due(db, app.state.settings, NOW) == 2
        assert calls.count("http://homeassistant.local:8123/api/webhook/mediatracker") == 1
        assert calls.count("http://homeassistant.local:8123/api/webhook/telephone") == 1
        skipped = db.query(WebhookDelivery).filter(WebhookDelivery.last_response == "doublon").count()
        assert skipped == 4
        calls.clear()
        record_event(
            db,
            "download.failed",
            level="warning",
            media_id=media_id,
            item_id=item_id,
            dedupe_key="download.failed:3",
            when=NOW,
            payload={**payload, "error_code": "DISK_FULL", "error": "Espace disque insuffisant."},
        )
        db.commit()
        assert deliver_due(db, app.state.settings, NOW) == 2
        assert calls.count("http://homeassistant.local:8123/api/webhook/mediatracker") == 1
        assert calls.count("http://homeassistant.local:8123/api/webhook/telephone") == 1
    finally:
        db.close()
