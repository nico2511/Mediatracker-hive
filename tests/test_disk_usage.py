"""Folder sizes stay cached. A refresh does not hold the request open."""

import json
import os
import threading
import time
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path

from tests.conftest import csrf, login

from mediatracker.core.disk_usage import measure_paths, reset_state
from mediatracker.db.models import Setting

NOW = datetime(2026, 10, 17, 4, 30, tzinfo=timezone.utc)
Usage = namedtuple("Usage", "total used free")


def test_fake_folders_report_their_size_and_the_volume_gap(tmp_path):
    movies = tmp_path / "Films"
    series = tmp_path / "Series"
    incomplete = tmp_path / "downloads" / "incomplete"
    trash = tmp_path / "Corbeille"
    movies.mkdir()
    series.mkdir()
    incomplete.mkdir(parents=True)
    trash.mkdir()
    (movies / "A.mkv").write_bytes(b"a" * 1000)
    (series / "B.mkv").write_bytes(b"b" * 2000)
    (incomplete / "part.mkv").write_bytes(b"c" * 300)
    (incomplete / "part.mkv.aria2").write_bytes(b"d" * 10)
    (incomplete / "outside.mkv").symlink_to(movies / "A.mkv")
    (trash / "old.mkv").write_bytes(b"e" * 400)

    def device(path: Path) -> int:
        text = str(path)
        if "incomplete" in text or "Corbeille" in text:
            return 2
        return 1

    def usage(path: Path):
        if device(path) == 2:
            return Usage(10_000, 4_000, 6_000)
        return Usage(50_000, 20_000, 30_000)

    def mount(path: Path) -> str:
        return "/downloads" if device(path) == 2 else "/data"

    report = measure_paths(
        {"movies": movies, "series": series, "incomplete": incomplete, "trash": trash},
        now=NOW,
        usage=usage,
        device=device,
        mount=mount,
    )
    def allocated(path: Path) -> int:
        return path.lstat().st_blocks * 512

    sizes = {row["key"]: row["bytes"] for row in report["folders"]}
    assert sizes == {
        "movies": allocated(movies / "A.mkv"),
        "series": allocated(series / "B.mkv"),
        "incomplete": allocated(incomplete / "part.mkv") + allocated(incomplete / "part.mkv.aria2"),
        "trash": allocated(trash / "old.mkv"),
    }
    reserved = {row["key"]: row["reserved_bytes"] for row in report["folders"]}
    assert reserved == {"movies": 1000, "series": 2000, "incomplete": 310, "trash": 400}
    names = {row["name"] for row in next(row for row in report["folders"] if row["key"] == "incomplete")["files"]}
    assert names == {"part.mkv", "part.mkv.aria2"}
    assert report["free_bytes"] == 6_000
    by_mount = {row["mount"]: row for row in report["volumes"]}
    library = by_mount["/data"]
    assert library["device"] == "1"
    assert library["total_bytes"] == 50_000
    assert library["used_bytes"] == 20_000
    assert library["free_bytes"] == 30_000
    assert library["measured_bytes"] == sizes["movies"] + sizes["series"]
    assert library["other_bytes"] == 20_000 - library["measured_bytes"]
    assert library["folders"] == ["Films", "Séries"]
    downloads = by_mount["/downloads"]
    assert downloads["measured_bytes"] == sizes["incomplete"] + sizes["trash"]
    assert downloads["other_bytes"] == 4_000 - downloads["measured_bytes"]
    assert downloads["folders"] == ["Téléchargements incomplets", "Corbeille"]
    assert "Téléchargements incomplets" in {row["label"] for row in report["folders"]}


def test_disk_card_reads_the_cache_without_walking(client, app, monkeypatch):
    reset_state()
    login(client)
    cached = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "error": "",
        "free_bytes": 42,
        "folders": [],
        "volumes": [],
    }
    db = app.state.session_factory()
    try:
        db.add(
            Setting(
                key="disk.folder_usage",
                value=json.dumps(cached),
                is_secret=False,
                updated_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
    finally:
        db.close()

    def boom(_path):
        raise AssertionError("le parcours ne doit pas partir")

    monkeypatch.setattr("mediatracker.core.disk_usage.walk_tree", boom)
    listed = client.get("/api/v1/health/disk")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["free_bytes"] == 42
    assert body["running"] is False
    assert body["computed_at"]
    reset_state()


def test_refresh_answers_before_the_walk_finishes(client, app, monkeypatch):
    del app
    reset_state()
    login(client)
    started = threading.Event()
    release = threading.Event()

    def slow(path: Path):
        started.set()
        assert release.wait(5)
        return 0, 0, not path.exists(), []

    monkeypatch.setattr("mediatracker.core.disk_usage.walk_tree", slow)
    began = time.monotonic()
    response = client.post("/api/v1/health/disk", headers=csrf(client))
    assert response.status_code == 200, response.text
    assert time.monotonic() - began < 1
    assert response.json()["running"] is True
    assert started.wait(2)
    release.set()
    body: dict = {}
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        body = client.get("/api/v1/health/disk").json()
        if not body["running"] and body["computed_at"]:
            break
        time.sleep(0.05)
    assert body["running"] is False
    assert {row["key"] for row in body["folders"]} == {"movies", "series", "incomplete", "trash"}
    labels = {row["key"]: row["label"] for row in body["folders"]}
    assert labels["incomplete"] == "Téléchargements incomplets"
    assert labels["trash"] == "Corbeille"
    assert body["volumes"]
    assert "mount" in body["volumes"][0]
    assert body["volumes"][0]["total_bytes"] >= body["volumes"][0]["free_bytes"]
    reset_state()


def test_sparse_incomplete_splits_written_and_reserved_and_names_the_job(tmp_path):
    movies = tmp_path / "Films"
    series = tmp_path / "Series"
    incomplete = tmp_path / "downloads" / "incomplete"
    nested = incomplete / "lot"
    trash = tmp_path / "Corbeille"
    for path in (movies, series, nested, trash):
        path.mkdir(parents=True)
    (movies / "A.mkv").write_bytes(b"a")
    (series / "B.mkv").write_bytes(b"b")
    (trash / "old.mkv").write_bytes(b"e")
    sparse = incomplete / "Brume.mkv"
    fd = os.open(sparse, os.O_CREAT | os.O_WRONLY, 0o644)
    os.truncate(fd, 8 * 1024 * 1024)
    os.pwrite(fd, b"x" * 4096, 0)
    os.close(fd)
    (nested / "Nappe.mkv").write_bytes(b"n" * 100)
    (incomplete / "Brume.mkv.aria2").write_bytes(b"c" * 20)
    givre = incomplete / "Givre.mkv"
    givre.write_bytes(b"g" * 50)
    os.link(givre, incomplete / "Givre-alias.mkv")
    (incomplete / "dehors.mkv").symlink_to(movies / "A.mkv")
    info = sparse.lstat()
    written = info.st_blocks * 512
    assert info.st_size == 8 * 1024 * 1024
    assert written < info.st_size

    report = measure_paths(
        {"movies": movies, "series": series, "incomplete": incomplete, "trash": trash},
        now=NOW,
        usage=lambda _path: Usage(100, 50, 50),
        device=lambda _path: 1,
        mount=lambda _path: "/data",
        jobs={
            "Brume.mkv": {"job_id": 7, "job_state": "downloading"},
            "Nappe.mkv": {"job_id": 8, "job_state": "queued"},
        },
        busy={"Brume.mkv", "Nappe.mkv"},
    )
    folder = next(row for row in report["folders"] if row["key"] == "incomplete")
    assert folder["reserved_bytes"] > folder["written_bytes"]
    assert folder["bytes"] == folder["written_bytes"]
    # The hard link is the same inode. The symlink points outside and is ignored.
    once = written + (nested / "Nappe.mkv").lstat().st_blocks * 512
    once += (incomplete / "Brume.mkv.aria2").lstat().st_blocks * 512
    once += givre.lstat().st_blocks * 512
    assert folder["written_bytes"] == once
    assert folder["reserved_bytes"] == info.st_size + 100 + 20 + 50
    by_name = {row["name"]: row for row in folder["files"]}
    assert len({"Givre.mkv", "Givre-alias.mkv"} & set(by_name)) == 1
    assert "dehors.mkv" not in by_name
    assert by_name["Brume.mkv"]["written_bytes"] == written
    assert by_name["Brume.mkv"]["reserved_bytes"] == info.st_size
    assert by_name["Brume.mkv"]["orphan"] is False
    assert by_name["Brume.mkv"]["job_id"] == 7
    assert by_name["Brume.mkv"]["job_state"] == "downloading"
    assert by_name["lot/Nappe.mkv"]["job_id"] == 8
    assert by_name["lot/Nappe.mkv"]["orphan"] is False
    assert by_name["Brume.mkv.aria2"]["job_id"] == 7
    assert by_name["Brume.mkv.aria2"]["orphan"] is False
    givre = next(row for row in by_name.values() if Path(row["name"]).stem.startswith("Givre"))
    assert givre["orphan"] is True


def test_busy_incomplete_file_is_not_orphan_even_with_an_older_done_row(client, app, tmp_path):
    from datetime import datetime, timezone

    from mediatracker.core.disk_usage import download_index, measure_paths
    from mediatracker.core.incomplete import busy_names
    from mediatracker.db.models import Download, Media, MediaItem

    del client
    incomplete = tmp_path / "downloads" / "incomplete"
    incomplete.mkdir(parents=True)
    active = incomplete / "Flux.mkv"
    active.write_bytes(b"a" * 200_000)
    (incomplete / "Flux.mkv.aria2").write_bytes(b"ctrl")
    stale = incomplete / "Relic.mkv"
    stale.write_bytes(b"s" * 200_000)
    now = datetime(2026, 10, 20, 8, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        show = Media(type="series", title="Signal", year=2026, followed=False)
        db.add(show)
        db.flush()
        item = MediaItem(media_id=show.id, season=1, episode=1, title="pilot", state="downloading")
        db.add(item)
        db.flush()
        db.add(
            Download(
                media_item_id=item.id,
                state="done",
                filename="Flux.mkv",
                created_at=now,
            )
        )
        db.add(
            Download(
                media_item_id=item.id,
                state="downloading",
                filename="Flux.mkv",
                created_at=now,
            )
        )
        db.add(
            Download(
                media_item_id=item.id,
                state="done",
                filename="Relic.mkv",
                created_at=now,
            )
        )
        db.commit()
        jobs = download_index(db)
        busy = busy_names(db)
        report = measure_paths(
            {
                "movies": tmp_path / "Films",
                "series": tmp_path / "Series",
                "incomplete": incomplete,
                "trash": tmp_path / "Corbeille",
            },
            now=now,
            usage=lambda _path: Usage(100, 50, 50),
            device=lambda _path: 1,
            mount=lambda _path: "/data",
            jobs=jobs,
            busy=busy,
        )
    finally:
        db.close()
    folder = next(row for row in report["folders"] if row["key"] == "incomplete")
    by_name = {row["name"]: row for row in folder["files"]}
    assert by_name["Flux.mkv"]["orphan"] is False
    assert by_name["Flux.mkv"]["job_state"] == "downloading"
    assert by_name["Flux.mkv.aria2"]["orphan"] is False
    assert by_name["Relic.mkv"]["orphan"] is True
    assert by_name["Relic.mkv"]["job_state"] == "done"


def test_post_processing_path_keeps_the_file_off_the_orphan_list(client, app, tmp_path):
    from datetime import datetime, timezone

    from mediatracker.core.disk_usage import download_index, measure_paths
    from mediatracker.core.incomplete import busy_names
    from mediatracker.db.models import Download, Media, MediaItem

    del client
    incomplete = tmp_path / "downloads" / "incomplete"
    incomplete.mkdir(parents=True)
    moving = incomplete / "lot" / "Nappe.mkv"
    moving.parent.mkdir(parents=True)
    moving.write_bytes(b"n" * 100_000)
    now = datetime(2026, 10, 20, 9, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        show = Media(type="series", title="Brume", year=2026, followed=False)
        db.add(show)
        db.flush()
        item = MediaItem(media_id=show.id, season=1, episode=2, title="suite", state="downloading")
        db.add(item)
        db.flush()
        db.add(
            Download(
                media_item_id=item.id,
                state="post_processing",
                filename="Nappe.mkv",
                error=str(moving),
                created_at=now,
            )
        )
        db.commit()
        jobs = download_index(db)
        busy = busy_names(db)
        report = measure_paths(
            {
                "movies": tmp_path / "Films",
                "series": tmp_path / "Series",
                "incomplete": incomplete,
                "trash": tmp_path / "Corbeille",
            },
            now=now,
            usage=lambda _path: Usage(100, 50, 50),
            device=lambda _path: 1,
            mount=lambda _path: "/data",
            jobs=jobs,
            busy=busy,
        )
    finally:
        db.close()
    folder = next(row for row in report["folders"] if row["key"] == "incomplete")
    row = next(item for item in folder["files"] if item["name"] == "lot/Nappe.mkv")
    assert row["orphan"] is False
    assert row["job_state"] == "post_processing"
