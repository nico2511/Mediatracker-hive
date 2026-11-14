"""Orphan files in the incomplete folder are previewed, then moved to the trash."""

import errno
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests.conftest import csrf, login

from mediatracker.core.incomplete import maybe_clean_incomplete, preview_incomplete
from mediatracker.db.models import Download, Event, Media, MediaItem, Setting, TrashEntry

NOW = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)


def _layout(app) -> tuple[Path, Path, Path]:
    data = Path(app.state.settings.data_dir)
    return data / "downloads" / "incomplete", data / "media" / "Series", data / "media" / "Films"


def test_incomplete_preview_keeps_active_files_and_trashes_orphans(client, app):
    login(client)
    incomplete, series, movies = _layout(app)
    incomplete.mkdir(parents=True, exist_ok=True)
    library = series / "Lanternes Démo (2026)" / "Season 01" / "Lanternes Démo (2026) - S01E08.mkv"
    library.parent.mkdir(parents=True)
    library.write_bytes(b"library" * 1000)
    present = incomplete / "Lanternes Démo.S01E08.FiNAL.1080p.WEB.mkv"
    present.write_bytes(b"p" * 200_000)
    tiny = incomplete / "partial.mkv"
    tiny.write_bytes(b"x" * 3 * 1024)
    aria = incomplete / "orphan.mkv.aria2"
    aria.write_bytes(b"control")
    active = incomplete / "active.mkv"
    active.write_bytes(b"a" * 200_000)
    active_aria = incomplete / "active.mkv.aria2"
    active_aria.write_bytes(b"busy")
    later = incomplete / "later.mkv"
    later.write_bytes(b"l" * 200_000)
    stale = incomplete / "stale.mkv"
    stale.write_bytes(b"s" * 200_000)
    old = (NOW - timedelta(days=4)).timestamp()
    os.utime(stale, (old, old))
    hidden = incomplete / ".hidden.mkv"
    hidden.write_bytes(b"h" * 10)
    film = movies / "Solo (2024)" / "Solo (2024).mkv"
    film.parent.mkdir(parents=True)
    film.write_bytes(b"film" * 1000)
    film_copy = incomplete / "Solo (2024).mkv"
    film_copy.write_bytes(b"f" * 200_000)

    db = app.state.session_factory()
    try:
        show = Media(type="series", title="Lanternes Démo", year=2026, followed=False)
        db.add(show)
        db.flush()
        item = MediaItem(media_id=show.id, season=1, episode=9, title="autre", state="missing")
        db.add(item)
        db.flush()
        db.add(Download(media_item_id=item.id, state="downloading", filename="active.mkv", created_at=NOW))
        db.commit()
        plan = preview_incomplete(db, app.state.settings, now=NOW)
        reasons = {Path(item.path).name: item.reason for item in plan}
        assert reasons[present.name] == "Déjà dans la bibliothèque."
        assert reasons[film_copy.name] == "Déjà dans la bibliothèque."
        assert "minuscule" in reasons[tiny.name]
        assert reasons[aria.name] == "Contrôle aria2 orphelin."
        assert "3 jours" in reasons[stale.name]
        assert active.name not in reasons
        assert active_aria.name not in reasons
        assert later.name not in reasons
        assert hidden.name not in reasons

        listed = client.get("/api/v1/library/incomplete")
        assert listed.status_code == 200, listed.text
        assert {row["name"] for row in listed.json()["files"]} == set(reasons)
        assert listed.json()["moved"] == 0

        refused = client.post("/api/v1/library/incomplete", json={"validated": False}, headers=csrf(client))
        assert refused.status_code == 200, refused.text
        assert refused.json()["moved"] == 0
        assert present.is_file() and tiny.is_file() and aria.is_file()

        applied = client.post("/api/v1/library/incomplete", json={"validated": True}, headers=csrf(client))
        assert applied.status_code == 200, applied.text
        body = applied.json()
        assert body["moved"] == len(reasons)
        assert body["failed"] == 0
        assert body["errors"] == []
        assert body["files"] == []
        from mediatracker.core.storage import storage_layout

        assert body["trash"] == str(storage_layout(db, app.state.settings).trash_dir)
        for path in (present, tiny, aria, stale, film_copy):
            assert not path.exists()
        assert active.is_file() and later.is_file() and hidden.is_file() and library.is_file()
        db.expire_all()
        held = db.query(TrashEntry).all()
        assert len(held) == len(reasons)
        assert {row.reason for row in held} == {"incomplete"}
        assert all(Path(row.trash_path).is_file() for row in held)
    finally:
        db.close()


def test_incomplete_uses_the_saved_trash_delay(client, app):
    login(client)
    incomplete, _series, _movies = _layout(app)
    incomplete.mkdir(parents=True, exist_ok=True)
    stale = incomplete / "stale-delay.mkv"
    stale.write_bytes(b"s" * 200_000)
    old = (NOW - timedelta(days=4)).timestamp()
    os.utime(stale, (old, old))
    db = app.state.session_factory()
    try:
        row = db.get(Setting, "trash_retention_days")
        assert row is not None
        row.value = "30"
        db.commit()
        applied = client.post("/api/v1/library/incomplete", json={"validated": True}, headers=csrf(client))
        assert applied.status_code == 200, applied.text
        db.expire_all()
        entry = db.query(TrashEntry).one()
        assert entry.purge_after - entry.deleted_at == timedelta(days=30)
        assert not stale.exists()
        assert Path(entry.trash_path).is_file()
    finally:
        db.close()


def test_incomplete_auto_is_optional_and_hourly(client, app):
    del client
    incomplete, _series, _movies = _layout(app)
    incomplete.mkdir(parents=True, exist_ok=True)
    stale = incomplete / "stale.mkv"
    stale.write_bytes(b"s" * 200_000)
    old = (NOW - timedelta(days=4)).timestamp()
    os.utime(stale, (old, old))
    db = app.state.session_factory()
    try:
        assert maybe_clean_incomplete(db, app.state.settings, now=NOW) == 0
        assert stale.is_file()
        row = db.get(Setting, "incomplete_cleanup_enabled")
        assert row is not None and row.value == "false"
        row.value = "true"
        db.commit()
        assert maybe_clean_incomplete(db, app.state.settings, now=NOW) == 1
        assert not stale.exists()
        nxt = incomplete / "again.mkv"
        nxt.write_bytes(b"n" * 200_000)
        os.utime(nxt, (old, old))
        assert maybe_clean_incomplete(db, app.state.settings, now=NOW + timedelta(minutes=30)) == 0
        assert nxt.is_file()
        assert maybe_clean_incomplete(db, app.state.settings, now=NOW + timedelta(hours=2)) == 1
        assert not nxt.exists()
    finally:
        db.close()


def _put(db, key: str, value: str) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value, is_secret=False, updated_at=NOW))
    else:
        row.value = value
        row.updated_at = NOW


def test_trash_inside_the_series_tree_does_not_come_back(client, app):
    """A non-dot trash folder placed in Séries used to be scanned as episodes.

    On the NAS the trash is ``/data/.mediatracker/trash``, next to ``Séries``.
    ``os.walk`` skips that hidden directory, but a corbeille named ``Corbeille``
    inside the series tree, and ``rglob`` into ``.mediatracker``, put the moved
    files back into Bibliothèque. The scan, the review and Jellyfin skip it.
    """
    login(client)
    incomplete, series, _movies = _layout(app)
    incomplete.mkdir(parents=True, exist_ok=True)
    trash = series / "Corbeille"
    library = {
        "North Harbor (2024)": (1, 4, 5),
        "Cedar Post (2025)": (5, 6, 8),
    }
    for title, episodes in library.items():
        for episode in episodes:
            path = series / title / "Season 01" / f"{title} - S01E{episode:02d}.mkv"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"library")
    orphans = [
        "North.Harbor.S01E01.mkv",
        "North.Harbor.S01E04.mkv",
        "North.Harbor.S01E05.mkv",
        "Cedar.Post.S01E05.mkv",
        "Cedar.Post.S01E06.mkv",
        "Cedar.Post.S01E08.mkv",
        "North.Harbor.S01E01.mkv.aria2",
        "Cedar.Post.S01E05.mkv.aria2",
    ]
    for name in orphans:
        (incomplete / name).write_bytes(b"o" * 200_000)
    db = app.state.session_factory()
    try:
        _put(db, "trash_dir", str(trash))
        db.commit()
        listed = client.get("/api/v1/library/incomplete")
        assert listed.status_code == 200, listed.text
        preview = listed.json()
        assert preview["trash"] == str(trash)
        assert {row["name"] for row in preview["files"]} == set(orphans)
        assert {row["reason"] for row in preview["files"] if row["name"].endswith(".mkv")} == {
            "Déjà dans la bibliothèque."
        }

        applied = client.post("/api/v1/library/incomplete", json={"validated": True}, headers=csrf(client))
        assert applied.status_code == 200, applied.text
        body = applied.json()
        assert body["moved"] == len(orphans)
        assert body["failed"] == 0
        assert body["errors"] == []
        assert body["files"] == []
        assert body["trash"] == str(trash)
        for name in orphans:
            assert not (incomplete / name).exists()
            assert any(path.name == name for path in trash.rglob(name))
        for title, episodes in library.items():
            for episode in episodes:
                kept = series / title / "Season 01" / f"{title} - S01E{episode:02d}.mkv"
                assert kept.is_file()

        review = client.get("/api/v1/library/review")
        assert review.status_code == 200, review.text
        folders = [folder["name"] for row in review.json()["series"] for folder in row["folders"]]
        assert "Corbeille" not in folders
        shown = [item["name"] for row in review.json()["series"] for item in row["files"]]
        assert not any(name in shown for name in orphans)

        regroup = client.get("/api/v1/library/regroup")
        assert regroup.status_code == 200, regroup.text
        assert "Corbeille" not in regroup.text

        scanned = client.post("/api/v1/library/scan", headers=csrf(client))
        assert scanned.status_code == 200, scanned.text
        db.expire_all()
        from mediatracker.db.models import LibraryFile

        recorded = [row.path for row in db.query(LibraryFile).all()]
        assert recorded
        assert not any("Corbeille" in path for path in recorded)

        from mediatracker.core.library_video import find_episode_on_disk
        from mediatracker.core.storage import library_skip, storage_layout

        layout = storage_layout(db, app.state.settings)
        found = find_episode_on_disk(
            movies_dir=layout.movies_dir,
            series_dir=layout.series_dir,
            media_type="series",
            title="North Harbor",
            year=2024,
            season=1,
            episode=1,
            tmdb_id=None,
            skip=library_skip(layout),
        )
        assert found is not None
        assert "Corbeille" not in found.parts
        assert found.name == "North Harbor (2024) - S01E01.mkv"
    finally:
        db.close()


def test_dot_trash_is_not_already_in_the_library(client, app):
    """``rglob`` enters ``.mediatracker``. A copy that only exists there is not the library."""
    del client
    incomplete, series, _movies = _layout(app)
    incomplete.mkdir(parents=True, exist_ok=True)
    trash = series / ".mediatracker" / "trash"
    trash.mkdir(parents=True)
    (trash / "North.Harbor.S01E01.mkv").write_bytes(b"t" * 200_000)
    orphan = incomplete / "North.Harbor.S01E01.mkv"
    orphan.write_bytes(b"o" * 200_000)
    db = app.state.session_factory()
    try:
        _put(db, "trash_dir", str(trash))
        db.commit()
        plan = preview_incomplete(db, app.state.settings, now=NOW)
        assert orphan.name not in {item.name for item in plan}
    finally:
        db.close()


def test_one_move_failure_is_reported_and_the_others_still_move(client, app, monkeypatch):
    login(client)
    incomplete, _series, _movies = _layout(app)
    incomplete.mkdir(parents=True, exist_ok=True)
    good = incomplete / "good.mkv"
    bad = incomplete / "bad.mkv"
    old = (NOW - timedelta(days=4)).timestamp()
    for path in (good, bad):
        path.write_bytes(b"s" * 200_000)
        os.utime(path, (old, old))
    from mediatracker.core.incomplete import atomic_move

    real = atomic_move

    def flaky(src, dest):
        if src.name == "bad.mkv":
            raise OSError("CROSS_DEVICE")
        real(src, dest)

    monkeypatch.setattr("mediatracker.core.incomplete.atomic_move", flaky)
    applied = client.post("/api/v1/library/incomplete", json={"validated": True}, headers=csrf(client))
    assert applied.status_code == 200, applied.text
    body = applied.json()
    assert body["moved"] == 1
    assert body["failed"] == 1
    assert body["errors"] == ["bad.mkv : autre disque, laissé sur place."]
    assert [row["name"] for row in body["files"]] == ["bad.mkv"]
    assert bad.is_file()
    assert not good.exists()
    assert any(path.name == "good.mkv" for path in Path(body["trash"]).rglob("good.mkv"))
    db = app.state.session_factory()
    try:
        db.expire_all()
        held = db.query(TrashEntry).all()
        assert len(held) == 1
        assert Path(held[0].trash_path).name == "good.mkv"
    finally:
        db.close()


def test_auto_uses_the_button_rules_and_records_the_pass(client, app):
    """Orphans and tiny files go at once. A young file and an active download stay, with a reason."""
    login(client)
    incomplete, series, _movies = _layout(app)
    incomplete.mkdir(parents=True, exist_ok=True)
    library = series / "Lanternes Démo (2026)" / "Season 01" / "Lanternes Démo (2026) - S01E08.mkv"
    library.parent.mkdir(parents=True, exist_ok=True)
    library.write_bytes(b"library" * 1000)
    present = incomplete / "Lanternes Démo.S01E08.WEB.mkv"
    present.write_bytes(b"p" * 200_000)
    aria = incomplete / "orphan.mkv.aria2"
    aria.write_bytes(b"control")
    recent = incomplete / "recent.mkv"
    recent.write_bytes(b"r" * 200_000)
    fresh = datetime.now(timezone.utc)
    os.utime(recent, (fresh.timestamp(), fresh.timestamp()))
    active = incomplete / "active.mkv"
    active.write_bytes(b"a" * 200_000)
    active_aria = incomplete / "active.mkv.aria2"
    active_aria.write_bytes(b"busy")
    db = app.state.session_factory()
    try:
        show = Media(type="series", title="Lanternes Démo", year=2026, followed=False)
        db.add(show)
        db.flush()
        item = MediaItem(media_id=show.id, season=1, episode=9, title="autre", state="missing")
        db.add(item)
        db.flush()
        db.add(Download(media_item_id=item.id, state="downloading", filename="active.mkv", created_at=NOW))
        _put(db, "incomplete_cleanup_enabled", "true")
        db.commit()

        listed = client.get("/api/v1/library/incomplete")
        assert listed.status_code == 200, listed.text
        body = listed.json()
        assert body["files"] == []
        assert not present.exists() and not aria.exists()
        assert active.is_file() and active_aria.is_file() and recent.is_file()
        assert body["auto_enabled"] is True
        assert body["last_run_at"]
        assert "2 déplacé(s)" in body["auto_message"]
        assert "0 échec(s)" in body["auto_message"]
        assert "3 laissé(s)" in body["auto_message"]
        assert "moins de 3 jours" in body["auto_message"]
        assert "téléchargement en cours" in body["auto_message"]
        assert "contrôle aria2" in body["auto_message"]

        db.expire_all()
        events = db.query(Event).filter(Event.type == "incomplete.cleaned").all()
        assert len(events) == 1
        assert events[0].payload["moved"] == 2
        assert events[0].payload["failed"] == 0
        assert events[0].payload["kept"] == 3
        assert events[0].level == "info"

        again = client.get("/api/v1/library/incomplete")
        assert again.status_code == 200, again.text
        db.expire_all()
        assert db.query(Event).filter(Event.type == "incomplete.cleaned").count() == 1
        assert recent.is_file()
    finally:
        db.close()


def test_auto_records_a_move_that_could_not_leave_the_disk(client, app, monkeypatch):
    login(client)
    incomplete, _series, _movies = _layout(app)
    incomplete.mkdir(parents=True, exist_ok=True)
    stuck = incomplete / "stuck.mkv.aria2"
    stuck.write_bytes(b"control")
    db = app.state.session_factory()
    try:
        _put(db, "incomplete_cleanup_enabled", "true")
        db.commit()

        def locked(src, dest):
            del src, dest
            raise OSError(errno.EBUSY, "Text file busy")

        monkeypatch.setattr("mediatracker.core.incomplete.atomic_move", locked)
        assert maybe_clean_incomplete(db, app.state.settings, now=NOW) == 0
        assert stuck.is_file()
        db.expire_all()
        event = db.query(Event).filter(Event.type == "incomplete.cleaned").one()
        assert event.level == "warning"
        assert event.payload["failed"] == 1
        assert event.payload["moved"] == 0
        assert "encore ouvert par aria2" in event.payload["message"]
        listed = client.get("/api/v1/library/incomplete")
        assert listed.status_code == 200, listed.text
        body = listed.json()
        assert [row["name"] for row in body["files"]] == [stuck.name]
        assert "encore ouvert par aria2" in body["auto_message"]
        assert body["auto_enabled"] is True
    finally:
        db.close()


def test_jellyfin_cache_drops_a_file_that_lands_in_the_trash(client, app):
    login(client)
    _series_root = _layout(app)[1]
    trash = _series_root / "Corbeille"
    trash.mkdir(parents=True)
    kept = _series_root / "North Harbor (2024)" / "Season 01" / "North Harbor (2024) - S01E01.mkv"
    kept.parent.mkdir(parents=True)
    kept.write_bytes(b"keep")
    trashed = trash / "North.Harbor.S01E04.mkv"
    trashed.write_bytes(b"gone")
    user = "11111111-1111-1111-1111-111111111111"
    db = app.state.session_factory()
    try:
        _put(db, "trash_dir", str(trash))
        _put(db, "jellyfin_user_id", user)
        from mediatracker.db.models import JellyfinLibraryItem

        db.add(
            JellyfinLibraryItem(
                jellyfin_id="trash-ep",
                item_type="Episode",
                name="E04",
                path=str(trashed),
                local_path=str(trashed),
                series_id="series-s",
                season=1,
                episode=4,
                synced_at=NOW,
            )
        )
        db.commit()
        items = [
            {
                "Id": "trash-ep",
                "Type": "Episode",
                "Name": "E04",
                "Path": str(trashed),
                "SeriesId": "series-s",
                "ParentIndexNumber": 1,
                "IndexNumber": 4,
            },
            {
                "Id": "keep-ep",
                "Type": "Episode",
                "Name": "E01",
                "Path": str(kept),
                "SeriesId": "series-s",
                "ParentIndexNumber": 1,
                "IndexNumber": 1,
            },
        ]

        import httpx

        from mediatracker.integrations.jellyfin import JellyfinClient
        from mediatracker.library.jellyfin_align import refresh_cache

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/ScheduledTasks":
                return httpx.Response(200, json=[{"Key": "RefreshLibrary", "State": "Idle"}])
            if request.url.path == "/Library/VirtualFolders":
                return httpx.Response(200, json=[])
            if request.url.path.endswith("/Items") and request.method == "GET":
                return httpx.Response(200, json={"Items": items, "TotalRecordCount": len(items)})
            return httpx.Response(404, json={})

        jellyfin = JellyfinClient("http://jellyfin.test", "key", user, transport=httpx.MockTransport(handler))
        try:
            report = refresh_cache(db, app.state.settings, jellyfin, full=False, now=NOW)
        finally:
            jellyfin.close()
        assert report["refreshed"] == 1
        db.commit()
        assert db.get(JellyfinLibraryItem, "trash-ep") is None
        kept_row = db.get(JellyfinLibraryItem, "keep-ep")
        assert kept_row is not None
        assert kept_row.local_path == str(kept)
    finally:
        db.close()
