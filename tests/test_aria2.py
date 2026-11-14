import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from mediatracker.providers.downloaders.aria2 import Aria2Client, Aria2Config, Aria2Downloader


def test_aria2_image_disables_ipv6():
    script = Path(__file__).resolve().parents[1] / "docker" / "s6-rc.d" / "aria2" / "run"
    text = script.read_text(encoding="utf-8")
    assert "disable-ipv6=true" in text
    assert "rpc-listen-all=false" in text
    assert "rpc-listen-port=6800" in text


def test_aria2_run_session_options():
    """Session resume without re-queuing finished or removed GIDs (no force-save)."""
    script = Path(__file__).resolve().parents[1] / "docker" / "s6-rc.d" / "aria2" / "run"
    text = script.read_text(encoding="utf-8")
    assert "save-session=/config/aria2.session" in text
    assert "input-file=/config/aria2.session" in text
    assert "force-save=true" not in text
    assert "force-save=false" in text
    assert "save-not-found=false" in text
    assert "auto-file-renaming=false" in text
    assert "max-tries=5" in text
    assert "file-allocation=none" in text


def test_aria2_rpc_stays_on_localhost():
    Aria2Config(rpc_url="http://127.0.0.1:6800/jsonrpc", rpc_secret="secret")
    with pytest.raises(ValidationError):
        Aria2Config(rpc_url="http://files.example/jsonrpc", rpc_secret="secret")


def test_aria2_add_and_status_round_trip():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen["method"] = body["method"]
        seen["secret"] = body["params"][0]
        if body["method"] == "aria2.addUri":
            seen["url"] = body["params"][1][0]
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": "mt", "result": "gid1"})
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": "mt",
                "result": {
                    "status": "active",
                    "completedLength": "10",
                    "totalLength": "40",
                    "downloadSpeed": "5",
                    "files": [{"path": "/data/downloads/incomplete/Episode.mkv"}],
                },
            },
        )

    client = Aria2Client("http://127.0.0.1:6800/jsonrpc", "secret", transport=httpx.MockTransport(handler))
    gid = client.add_uri("https://cdn.example/secret.mkv", Path("/tmp"), "Episode.mkv")
    assert gid == "gid1"
    assert seen["secret"] == "token:secret"
    assert seen["url"] == "https://cdn.example/secret.mkv"
    status = Aria2Downloader(Aria2Config(rpc_secret="secret"), transport=httpx.MockTransport(handler)).status("gid1")
    assert status.state == "downloading"
    assert status.done_bytes == 10
    assert status.speed == 5
    assert status.path.endswith("Episode.mkv")


def test_aria2_add_uri_passes_http_headers():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["method"] == "aria2.addUri":
            seen["options"] = body["params"][2]
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": "mt", "result": "gid-h"})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": "mt", "result": "ok"})

    client = Aria2Client("http://127.0.0.1:6800/jsonrpc", "secret", transport=httpx.MockTransport(handler))
    headers = {"Hive-Ruche": "aa" * 16, "Hive-Fetch-Grant": "grant-opaque-token"}
    client.add_uri("https://peer.example/hive/files/" + "bb" * 16, Path("/tmp"), "piece.mkv", headers=headers)
    assert seen["options"]["header"] == [
        "Hive-Ruche: " + "aa" * 16,
        "Hive-Fetch-Grant: grant-opaque-token",
    ]


def test_aria2_refuses_a_redirect():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://evil.example/jsonrpc"})

    client = Aria2Client("http://127.0.0.1:6800/jsonrpc", "secret", transport=httpx.MockTransport(handler))
    from mediatracker.providers.downloaders.aria2 import Aria2Error

    with pytest.raises(Aria2Error) as caught:
        client.get_version()
    assert caught.value.code == "REDIRECT_BLOCKED"


def test_remove_drops_the_result_even_when_files_stay():
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        methods.append(body["method"])
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": "mt", "result": "OK"})

    client = Aria2Client("http://127.0.0.1:6800/jsonrpc", "secret", transport=httpx.MockTransport(handler))
    client.remove("gid-done", delete_files=False)
    assert methods == ["aria2.remove", "aria2.removeDownloadResult", "aria2.saveSession"]


def _aria2_missing_param_error() -> dict:
    return {"code": 1, "message": "The parameter at 0 is required but missing"}


def _require_tell_offset_num(params: list) -> tuple[int, int] | None:
    if len(params) < 2:
        return None
    return int(params[0]), int(params[1])


def test_list_queue_reads_active_waiting_and_stopped():
    calls: list[tuple[str, list]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method = body["method"]
        params = body["params"][1:]
        calls.append((method, params))
        if method == "aria2.tellActive":
            payload = [{"gid": "a1", "status": "active", "files": [{"path": "/data/incomplete/A.mkv"}]}]
        elif method == "aria2.tellWaiting":
            if _require_tell_offset_num(params) is None:
                return httpx.Response(
                    200,
                    json={"jsonrpc": "2.0", "id": "mt", "error": _aria2_missing_param_error()},
                )
            payload = [{"gid": "w1", "status": "waiting", "files": [{"path": "/data/incomplete/W.mkv"}]}]
        elif method == "aria2.tellStopped":
            if _require_tell_offset_num(params) is None:
                return httpx.Response(
                    200,
                    json={"jsonrpc": "2.0", "id": "mt", "error": _aria2_missing_param_error()},
                )
            payload = [{"gid": "s1", "status": "error", "files": [{"path": "/data/incomplete/S.mkv"}]}]
        else:
            payload = []
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": "mt", "result": payload})

    client = Aria2Client("http://127.0.0.1:6800/jsonrpc", "secret", transport=httpx.MockTransport(handler))
    entries = client.list_queue()
    assert [(entry.gid, entry.status, entry.filename) for entry in entries] == [
        ("a1", "active", "A.mkv"),
        ("w1", "waiting", "W.mkv"),
        ("s1", "error", "S.mkv"),
    ]
    assert calls[0] == ("aria2.tellActive", [["gid", "status", "files"]])
    assert calls[1][0] == "aria2.tellWaiting"
    assert calls[1][1][:2] == [0, 1000]
    assert calls[2][0] == "aria2.tellStopped"
    assert calls[2][1][:2] == [0, 1000]


def test_list_queue_paginates_waiting_and_stopped():
    page_size = 1000

    def page(gid_prefix: str, offset: int, num: int, total: int) -> list[dict]:
        start = offset
        end = min(offset + num, total)
        return [
            {
                "gid": f"{gid_prefix}{index}",
                "status": "waiting" if gid_prefix == "w" else "error",
                "files": [{"path": f"/data/incomplete/{gid_prefix}{index}.mkv"}],
            }
            for index in range(start, end)
        ]

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method = body["method"]
        params = body["params"][1:]
        if method == "aria2.tellActive":
            payload = []
        elif method == "aria2.tellWaiting":
            bounds = _require_tell_offset_num(params)
            if bounds is None:
                return httpx.Response(
                    200,
                    json={"jsonrpc": "2.0", "id": "mt", "error": _aria2_missing_param_error()},
                )
            offset, num = bounds
            payload = page("w", offset, num, page_size + 3)
        elif method == "aria2.tellStopped":
            bounds = _require_tell_offset_num(params)
            if bounds is None:
                return httpx.Response(
                    200,
                    json={"jsonrpc": "2.0", "id": "mt", "error": _aria2_missing_param_error()},
                )
            offset, num = bounds
            payload = page("s", offset, num, 2)
        else:
            payload = []
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": "mt", "result": payload})

    client = Aria2Client("http://127.0.0.1:6800/jsonrpc", "secret", transport=httpx.MockTransport(handler))
    entries = client.list_queue()
    waiting = [entry.gid for entry in entries if entry.gid.startswith("w")]
    stopped = [entry.gid for entry in entries if entry.gid.startswith("s")]
    assert len(waiting) == page_size + 3
    assert waiting[0] == "w0"
    assert waiting[-1] == f"w{page_size + 2}"
    assert stopped == ["s0", "s1"]


def test_remove_logs_rpc_errors_without_raising(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["method"] == "aria2.remove":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": "mt", "result": "OK"})
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": "mt", "error": {"code": 1, "message": "GID not found"}},
        )

    client = Aria2Client("http://127.0.0.1:6800/jsonrpc", "secret", transport=httpx.MockTransport(handler))
    with caplog.at_level("INFO", logger="mediatracker.providers.downloaders.aria2"):
        client.remove("missing-gid", delete_files=False)
    assert "removeDownloadResult ignoré" in caplog.text


def _sample_session() -> str:
    return "\n".join(
        [
            "https://cdn.example/north",
            "  gid=orphan",
            "  out=North.Harbor.S01E01.mkv",
            "https://cdn.example/active",
            "  gid=active-gid",
            "  out=Cedar.Post.S02E03.mkv",
            "",
        ]
    )


def test_session_filter_keeps_active_gid_and_drops_stale(tmp_path):
    from mediatracker.providers.downloaders.aria2 import filter_aria2_session_file

    session = tmp_path / "aria2.session"
    session.write_text(_sample_session(), encoding="utf-8")
    dropped = filter_aria2_session_file(session, keep_gids={"active-gid"}, keep_names=set())
    assert dropped == 1
    text = session.read_text(encoding="utf-8")
    assert "North.Harbor" not in text
    assert "gid=active-gid" in text
    assert (tmp_path / "aria2.session.bak").is_file()


def test_session_filter_keeps_active_filename(tmp_path):
    from mediatracker.providers.downloaders.aria2 import filter_aria2_session_file

    session = tmp_path / "aria2.session"
    session.write_text(_sample_session(), encoding="utf-8")
    dropped = filter_aria2_session_file(session, keep_gids=set(), keep_names={"North.Harbor.S01E01.mkv"})
    assert dropped == 1
    text = session.read_text(encoding="utf-8")
    assert "North.Harbor" in text
    assert "Cedar.Post" not in text


def test_session_filter_empty_or_missing_is_safe(tmp_path):
    from mediatracker.providers.downloaders.aria2 import filter_aria2_session_file

    missing = tmp_path / "missing.session"
    assert filter_aria2_session_file(missing, keep_gids=set(), keep_names=set()) == 0

    empty = tmp_path / "empty.session"
    empty.write_text("", encoding="utf-8")
    assert filter_aria2_session_file(empty, keep_gids=set(), keep_names=set()) == 0
    assert empty.read_text(encoding="utf-8") == ""


def test_session_filter_retries_transient_db_lock(app, tmp_path, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from mediatracker.core import aria2_session
    from mediatracker.core.aria2_session import prune_aria2_session_at_startup

    session = tmp_path / "aria2.session"
    session.write_text(_sample_session(), encoding="utf-8")
    settings = app.state.settings
    settings.config_dir = tmp_path

    db = app.state.session_factory()
    calls = {"count": 0}

    def flaky(_db):
        calls["count"] += 1
        if calls["count"] < 2:
            raise OperationalError("SELECT 1", {}, Exception("database is locked"))
        return set(), set()

    monkeypatch.setattr(aria2_session, "active_aria2_session_keys", flaky)
    prune_aria2_session_at_startup(db, settings)
    assert calls["count"] == 2
    text = session.read_text(encoding="utf-8")
    assert "North.Harbor" not in text
    assert "Cedar.Post" not in text


def test_session_filter_keeps_session_on_persistent_db_lock(app, tmp_path, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from mediatracker.core import aria2_session
    from mediatracker.core.aria2_session import prune_aria2_session_at_startup

    session = tmp_path / "aria2.session"
    original = _sample_session()
    session.write_text(original, encoding="utf-8")
    settings = app.state.settings
    settings.config_dir = tmp_path

    db = app.state.session_factory()

    def always_locked(_db):
        raise OperationalError("SELECT 1", {}, Exception("database is locked"))

    monkeypatch.setattr(aria2_session, "active_aria2_session_keys", always_locked)
    prune_aria2_session_at_startup(db, settings)
    assert session.read_text(encoding="utf-8") == original
    assert not (tmp_path / "aria2.session.unfiltered").exists()


def test_session_filter_resets_only_when_db_unreadable(app, tmp_path, monkeypatch):
    from sqlalchemy.exc import DatabaseError

    from mediatracker.core import aria2_session
    from mediatracker.core.aria2_session import prune_aria2_session_at_startup

    session = tmp_path / "aria2.session"
    session.write_text(_sample_session(), encoding="utf-8")
    settings = app.state.settings
    settings.config_dir = tmp_path

    db = app.state.session_factory()

    def corrupt(_db):
        raise DatabaseError("statement", {}, Exception("malformed"))

    monkeypatch.setattr(aria2_session, "active_aria2_session_keys", corrupt)
    prune_aria2_session_at_startup(db, settings)
    assert session.read_text(encoding="utf-8") == ""
    backup = tmp_path / "aria2.session.unfiltered"
    assert backup.is_file()
    assert "North.Harbor" in backup.read_text(encoding="utf-8")


def test_session_reset_aborted_when_backup_fails(app, tmp_path, monkeypatch):
    from sqlalchemy.exc import DatabaseError

    from mediatracker.core import aria2_session
    from mediatracker.core.aria2_session import prune_aria2_session_at_startup

    session = tmp_path / "aria2.session"
    original = _sample_session()
    session.write_text(original, encoding="utf-8")
    settings = app.state.settings
    settings.config_dir = tmp_path

    db = app.state.session_factory()

    def corrupt(_db):
        raise DatabaseError("statement", {}, Exception("malformed"))

    def fail_copy(*_args, **_kwargs):
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(aria2_session, "active_aria2_session_keys", corrupt)
    monkeypatch.setattr(aria2_session.shutil, "copy2", fail_copy)
    prune_aria2_session_at_startup(db, settings)
    assert session.read_text(encoding="utf-8") == original


def test_startup_prune_strips_error_session_entries_without_active_job(client, app, tmp_path):
    """Session blocks from failed jobs must not survive bootstrap when no download is active."""
    from mediatracker.core.aria2_session import prune_aria2_session_at_startup

    session = tmp_path / "aria2.session"
    session.write_text(_sample_session(), encoding="utf-8")
    settings = app.state.settings
    settings.config_dir = tmp_path

    db = app.state.session_factory()
    prune_aria2_session_at_startup(db, settings)
    text = session.read_text(encoding="utf-8")
    assert "North.Harbor" not in text
    assert "Cedar.Post" not in text


def test_session_filter_startup_with_active_download(client, app, tmp_path):
    from mediatracker.core.aria2_session import prune_aria2_session_at_startup
    from mediatracker.db.models import Download, Media, MediaItem

    session = tmp_path / "aria2.session"
    session.write_text(_sample_session(), encoding="utf-8")
    settings = app.state.settings
    settings.config_dir = tmp_path

    db = app.state.session_factory()
    media = Media(title="Cedar Post", type="series", tmdb_id=9001)
    db.add(media)
    db.flush()
    item = MediaItem(media_id=media.id, season=2, episode=3, state="downloading")
    db.add(item)
    db.flush()
    db.add(
        Download(
            media_item_id=item.id,
            state="downloading",
            handle="active-gid",
            filename="Cedar.Post.S02E03.mkv",
        )
    )
    db.commit()

    prune_aria2_session_at_startup(db, settings)
    text = session.read_text(encoding="utf-8")
    assert "Cedar.Post" in text
    assert "North.Harbor" not in text


def test_forget_files_stops_only_the_orphans():
    methods: list[tuple[str, list]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method = body["method"]
        params = body["params"][1:]
        methods.append((method, params))
        if method == "aria2.tellActive":
            result = [
                {"gid": "busy", "files": [{"path": "/data/downloads/incomplete/active.mkv"}]},
                {"gid": "orphan", "files": [{"path": "/data/downloads/incomplete/North.Harbor.S01E01.mkv"}]},
            ]
        elif method in {"aria2.tellWaiting", "aria2.tellStopped"}:
            if _require_tell_offset_num(params) is None:
                return httpx.Response(
                    200,
                    json={"jsonrpc": "2.0", "id": "mt", "error": _aria2_missing_param_error()},
                )
            result = []
        else:
            result = "OK"
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": "mt", "result": result})

    client = Aria2Client("http://127.0.0.1:6800/jsonrpc", "secret", transport=httpx.MockTransport(handler))
    forgotten = client.forget_files([Path("/data/downloads/incomplete/North.Harbor.S01E01.mkv.aria2")])
    assert forgotten == ["orphan"]
    assert ("aria2.remove", ["orphan"]) in methods
    assert ("aria2.forceRemove", ["busy"]) not in methods
    assert ("aria2.remove", ["busy"]) not in methods


def test_session_drop_keeps_unrelated_downloads(tmp_path):
    from mediatracker.providers.downloaders.aria2 import drop_session_downloads

    session = tmp_path / "aria2.session"
    session.write_text(
        "\n".join(
            [
                "https://cdn.example/north",
                "  gid=orphan",
                "  out=North.Harbor.S01E01.mkv",
                "https://cdn.example/other",
                "  gid=other",
                "  out=Cedar.Post.S02E03.mkv",
                "",
            ]
        ),
        encoding="utf-8",
    )
    dropped = drop_session_downloads(session, {"North.Harbor.S01E01.mkv.aria2"})
    assert dropped == 1
    text = session.read_text(encoding="utf-8")
    assert "North.Harbor" not in text
    assert "out=Cedar.Post.S02E03.mkv" in text
    assert (tmp_path / "aria2.session.bak").is_file()
    assert drop_session_downloads(session, set()) == 0
