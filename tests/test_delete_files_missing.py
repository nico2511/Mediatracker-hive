"""Manual delete when the file is already gone from disk."""

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from tests.conftest import csrf, login

from mediatracker.db.models import Event, LibraryFile, Media, MediaItem, TrashEntry


def _media(db, **kwargs):
    row = Media(type=kwargs.pop("type", "film"), title=kwargs.pop("title", "Film"), **kwargs)
    db.add(row)
    db.flush()
    return row


def _item(db, media, path: str | None, *, season=None, episode=None):
    resolved = str(Path(path).resolve()) if path else None
    row = MediaItem(
        media_id=media.id,
        season=season,
        episode=episode,
        title=media.title if episode is None else f"épisode {episode}",
        state="downloaded",
        downloaded=path is not None,
        downloaded_at=datetime.now(timezone.utc),
        file_path=resolved,
        watched=True,
        watched_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.flush()
    return row


def test_manual_delete_absent_file_skips_trash(client, app):
    login(client)
    data = Path(app.state.settings.data_dir)
    movies = data / "media" / "Films"
    ghost = movies / "Brume/Brume.mkv"
    db = app.state.session_factory()
    try:
        media = _media(db, title="Brume")
        item = _item(db, media, str(ghost))
        library = LibraryFile(
            path=str(ghost.resolve()),
            size=0,
            parsed_title="Brume",
            media_type="film",
            media_id=media.id,
            media_item_id=item.id,
            status="matched",
            confidence="high",
            seen_at=datetime.now(timezone.utc),
        )
        db.add(library)
        db.commit()
        media_id = media.id
        item_id = item.id
        library_id = library.id
    finally:
        db.close()

    assert not ghost.is_file()
    removed = client.post(
        "/api/v1/cleanup/files",
        json={"media_id": media_id, "scope": "media", "confirm": True},
        headers=csrf(client),
    )
    assert removed.status_code == 200, removed.text
    body = removed.json()
    assert body["moved"] == 0
    assert body["entries"] == []
    assert len(body["missing"]) == 1
    assert body["missing"][0]["message"] == "Fichier déjà absent du disque"
    assert ghost.name in body["missing"][0]["path"]

    db = app.state.session_factory()
    try:
        assert db.scalars(select(TrashEntry)).all() == []
        row = db.get(LibraryFile, library_id)
        assert row.status == "missing"
        assert row.missing_at is not None
        item = db.get(MediaItem, item_id)
        assert item.downloaded is False
        assert item.file_path is None
        event = db.scalar(select(Event).where(Event.type == "cleanup.missing"))
        assert event is not None
        assert event.payload["message"] == "Fichier déjà absent du disque"
    finally:
        db.close()

    fiche = client.get(f"/api/v1/media/{media_id}")
    assert fiche.status_code == 200
    assert fiche.json()["availability"] != "on_disk"


def test_manual_delete_mixed_present_and_absent(client, app):
    login(client)
    data = Path(app.state.settings.data_dir)
    series = data / "media" / "Series"
    kept = series / "Nimbus/Season 01/e1.mkv"
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_bytes(b"a" * 12)
    gone = series / "Nimbus/Season 01/e2.mkv"
    db = app.state.session_factory()
    try:
        show = _media(db, type="series", title="Nimbus")
        first = _item(db, show, str(kept), season=1, episode=1)
        second = _item(db, show, str(gone), season=1, episode=2)
        db.commit()
        media_id = show.id
        first_id = first.id
        second_id = second.id
    finally:
        db.close()

    removed = client.post(
        "/api/v1/cleanup/files",
        json={"media_id": media_id, "scope": "season", "season": 1, "confirm": True},
        headers=csrf(client),
    )
    assert removed.status_code == 200, removed.text
    body = removed.json()
    assert body["moved"] == 1
    assert body["bytes"] == 12
    assert len(body["missing"]) == 1
    assert not kept.is_file()
    assert not gone.is_file()

    db = app.state.session_factory()
    try:
        entries = list(db.scalars(select(TrashEntry)).all())
        assert len(entries) == 1
        assert "e1.mkv" in entries[0].original_path
        first = db.get(MediaItem, first_id)
        second = db.get(MediaItem, second_id)
        assert first.downloaded is False
        assert second.downloaded is False
        events = list(db.scalars(select(Event).where(Event.type == "cleanup.missing")).all())
        assert len(events) == 1
        assert events[0].item_id == second_id
        done = db.scalar(select(Event).where(Event.type == "cleanup.done"))
        assert done is not None
    finally:
        db.close()


def test_fiche_not_on_disk_when_every_file_was_already_missing(client, app):
    login(client)
    data = Path(app.state.settings.data_dir)
    movies = data / "media" / "Films"
    claimed = movies / "Cendre/Cendre.mkv"
    db = app.state.session_factory()
    try:
        media = _media(db, title="Cendre", followed=True)
        _item(db, media, str(claimed))
        db.commit()
        media_id = media.id
    finally:
        db.close()

    listed_before = client.get("/api/v1/media")
    row_before = next(item for item in listed_before.json()["items"] if item["id"] == media_id)
    assert row_before["availability"] == "on_disk"
    assert row_before["watched"] is True
    assert row_before["on_disk"] is True

    removed = client.post(
        "/api/v1/cleanup/files",
        json={"media_id": media_id, "scope": "media", "confirm": True},
        headers=csrf(client),
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["missing"]

    fiche = client.get(f"/api/v1/media/{media_id}")
    assert fiche.json()["availability"] == "waiting"
    assert fiche.json()["watched"] is True
    assert fiche.json()["on_disk"] is False
    listed_after = client.get("/api/v1/media")
    row_after = next(item for item in listed_after.json()["items"] if item["id"] == media_id)
    assert row_after["availability"] == "waiting"
    assert row_after["watched"] is True
    assert row_after["on_disk"] is False
