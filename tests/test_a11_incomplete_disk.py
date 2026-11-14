"""Incomplete list totals match the disk card; active jobs stay off the trash list."""

import os
from datetime import datetime, timezone
from pathlib import Path

from tests.conftest import csrf, login

from mediatracker.core.disk_usage import download_index, measure_paths, walk_tree
from mediatracker.core.incomplete import busy_names, incomplete_snapshot
from mediatracker.db.models import Download, Media, MediaItem

NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)


def _layout(app) -> Path:
    return Path(app.state.settings.data_dir) / "downloads" / "incomplete"


def test_incomplete_list_total_matches_disk_card_and_skips_active_job(client, app):
    login(client)
    incomplete = _layout(app)
    incomplete.mkdir(parents=True, exist_ok=True)
    orphan = incomplete / "Relic.mkv"
    orphan.write_bytes(b"o" * 50_000)
    active = incomplete / "Flux.mkv"
    active.write_bytes(b"a" * 80_000)
    (incomplete / "Flux.mkv.aria2").write_bytes(b"ctrl")
    sparse = incomplete / "Brume.mkv"
    fd = os.open(sparse, os.O_CREAT | os.O_WRONLY, 0o644)
    os.truncate(fd, 4 * 1024 * 1024)
    os.pwrite(fd, b"x" * 4096, 0)
    os.close(fd)
    old = (NOW.timestamp() - 5 * 86400)
    os.utime(orphan, (old, old))
    os.utime(sparse, (old, old))

    db = app.state.session_factory()
    try:
        show = Media(type="series", title="Signal", year=2026, followed=False)
        db.add(show)
        db.flush()
        item = MediaItem(media_id=show.id, season=1, episode=1, title="pilot", state="downloading")
        db.add(item)
        db.flush()
        db.add(Download(media_item_id=item.id, state="downloading", filename="Flux.mkv", created_at=NOW))
        db.commit()

        snap = incomplete_snapshot(db, app.state.settings, now=NOW)
        folder_written, folder_reserved, _missing, _files = walk_tree(incomplete)
        assert snap.folder_written_bytes == folder_written
        assert snap.folder_reserved_bytes == folder_reserved
        assert snap.total_written_bytes == sum(file.written_bytes for file in snap.files)
        names = {Path(file.path).name for file in snap.files}
        assert "Flux.mkv" not in names
        assert "Flux.mkv.aria2" not in names
        assert orphan.name in names and sparse.name in names

        disk = measure_paths(
            {
                "movies": Path(app.state.settings.data_dir) / "media" / "Films",
                "series": Path(app.state.settings.data_dir) / "media" / "Series",
                "incomplete": incomplete,
                "trash": Path(app.state.settings.data_dir) / "Corbeille",
            },
            now=NOW,
            jobs=download_index(db),
            busy=busy_names(db),
        )
        incomplete_row = next(row for row in disk["folders"] if row["key"] == "incomplete")
        assert incomplete_row["written_bytes"] == snap.folder_written_bytes

        listed = client.get("/api/v1/library/incomplete")
        assert listed.status_code == 200, listed.text
        body = listed.json()
        assert body["folder_written_bytes"] == snap.folder_written_bytes
        assert body["total_written_bytes"] == snap.total_written_bytes
        assert {row["name"] for row in body["files"]} == names
        for row in body["files"]:
            assert row["written_bytes"] > 0
            assert row["orphan"] is True

        applied = client.post(
            "/api/v1/library/incomplete",
            json={"validated": True, "paths": [snap.files[0].path]},
            headers=csrf(client),
        )
        assert applied.status_code == 200, applied.text
        assert applied.json()["moved"] == 1
        assert applied.json()["trash_ids"]
    finally:
        db.close()


def test_library_view_keeps_incomplete_refresh_and_apply_order():
    source = (Path(__file__).resolve().parents[1] / "frontend" / "src" / "views" / "LibraryView.vue").read_text(
        encoding="utf-8"
    )
    apply = source.split("async function applyIncomplete()", 1)[1].split("async function undoIncompleteTrash", 1)[0]
    assert apply.index("incomplete.value = result") < apply.index("await analyze()")
    analyze = source.split("async function analyze()", 1)[1].split("async function refreshJellyfin", 1)[0]
    assert "loadIncomplete" in analyze
    assert analyze.index("/api/v1/library/review") < analyze.index("loadIncomplete")
