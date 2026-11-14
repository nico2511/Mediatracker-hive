"""Deleting a fiche cancels jobs and drops history. Files stay, or move to the trash."""

from datetime import datetime, timezone
from pathlib import Path

from tests.conftest import csrf, login

from mediatracker.db.models import Download, Event, LibraryRename, Media, MediaItem, TrashEntry
from mediatracker.events import record_event


class _Downloader:
    def __init__(self) -> None:
        self.removed: list[str] = []

    def remove(self, handle: str, delete_files: bool = False) -> None:
        del delete_files
        self.removed.append(handle)


def _downloader(db, settings, client):
    del db, settings, client
    return None, _downloader.worker


def test_delete_keeps_files_and_the_rename_journal(client, app, monkeypatch):
    worker = _Downloader()
    _downloader.worker = worker
    monkeypatch.setattr("mediatracker.core.pipeline._downloader", _downloader)
    login(client)
    created = client.post(
        "/api/v1/media",
        json={"type": "film", "title": "À garder", "year": 2020},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    media_id = created.json()["id"]
    other = client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Voisine", "year": 2021},
        headers=csrf(client),
    )
    assert other.status_code == 201, other.text
    films = Path(app.state.settings.data_dir) / "media" / "Films"
    path = films / "À garder (2020)" / "À garder (2020).mkv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"keep-me")
    now = datetime(2026, 10, 13, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        item = MediaItem(
            media_id=media_id,
            title="À garder",
            state="downloaded",
            downloaded=True,
            file_path=str(path.resolve()),
        )
        db.add(item)
        db.flush()
        db.add(Download(media_item_id=item.id, state="pending", handle="aria-9", created_at=now))
        record_event(db, "media.note", media_id=media_id, payload={"title": "À garder"})
        record_event(db, "media.note", media_id=other.json()["id"], payload={"title": "Voisine"})
        db.add(LibraryRename(created_at=now, moves=[{"src": "a", "dest": "b", "original_name": "a.mkv"}]))
        db.commit()
        rename_id = db.query(LibraryRename).one().id
    finally:
        db.close()

    removed = client.delete(f"/api/v1/media/{media_id}", headers=csrf(client))
    assert removed.status_code == 204, removed.text
    assert client.get(f"/api/v1/media/{media_id}").status_code == 404
    assert path.is_file()
    assert path.read_bytes() == b"keep-me"
    assert worker.removed == ["aria-9"]
    db = app.state.session_factory()
    try:
        assert db.get(Media, media_id) is None
        assert db.query(MediaItem).filter(MediaItem.media_id == media_id).count() == 0
        assert db.query(Download).count() == 0
        assert db.query(Event).filter(Event.media_id == media_id).count() == 0
        assert db.query(Event).filter(Event.media_id == other.json()["id"]).count() >= 1
        journal = db.get(LibraryRename, rename_id)
        assert journal is not None
        assert journal.moves[0]["original_name"] == "a.mkv"
        assert db.get(Media, other.json()["id"]) is not None
    finally:
        db.close()


def test_delete_can_trash_files_and_bulk_removes_several_fiches(client, app, monkeypatch):
    worker = _Downloader()
    _downloader.worker = worker
    monkeypatch.setattr("mediatracker.core.pipeline._downloader", _downloader)
    login(client)
    first = client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Corbeille", "year": 2019},
        headers=csrf(client),
    )
    second = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Aussi", "year": 2018},
        headers=csrf(client),
    )
    kept = client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Reste", "year": 2017},
        headers=csrf(client),
    )
    assert first.status_code == second.status_code == kept.status_code == 201
    films = Path(app.state.settings.data_dir) / "media" / "Films"
    path = films / "Corbeille (2019)" / "Corbeille (2019).mkv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"trash-me")
    now = datetime(2026, 10, 13, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        item = MediaItem(
            media_id=first.json()["id"],
            title="Corbeille",
            state="downloaded",
            downloaded=True,
            file_path=str(path.resolve()),
        )
        db.add(item)
        db.flush()
        db.add(Download(media_item_id=item.id, state="queued", created_at=now))
        record_event(db, "media.followed", media_id=first.json()["id"], payload={"title": "Corbeille"})
        db.add(LibraryRename(created_at=now, moves=[{"src": "old", "dest": "new", "original_name": "old.mkv"}]))
        db.commit()
    finally:
        db.close()

    removed = client.delete(f"/api/v1/media/{first.json()['id']}?trash=true", headers=csrf(client))
    assert removed.status_code == 204, removed.text
    assert not path.exists()
    db = app.state.session_factory()
    try:
        entry = db.query(TrashEntry).one()
        assert Path(entry.trash_path).is_file()
        assert Path(entry.trash_path).read_bytes() == b"trash-me"
        assert entry.media_id is None
        assert db.query(LibraryRename).count() == 1
        assert db.query(Event).filter(Event.media_id == first.json()["id"]).count() == 0
        assert db.query(Event).filter(Event.media_id == second.json()["id"]).count() >= 1
        assert db.query(Download).count() == 0
    finally:
        db.close()

    bulk = client.request(
        "DELETE",
        "/api/v1/media/bulk",
        json={"ids": [second.json()["id"], kept.json()["id"]], "trash": False},
        headers=csrf(client),
    )
    assert bulk.status_code == 200, bulk.text
    assert bulk.json()["trash"] is False
    assert set(bulk.json()["deleted"]) == {second.json()["id"], kept.json()["id"]}
    assert client.get(f"/api/v1/media/{second.json()['id']}").status_code == 404
    assert client.get(f"/api/v1/media/{kept.json()['id']}").status_code == 404
    missing = client.request(
        "DELETE",
        "/api/v1/media/bulk",
        json={"ids": [second.json()["id"]], "trash": False},
        headers=csrf(client),
    )
    assert missing.status_code == 404
