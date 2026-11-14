import threading
import time
from pathlib import Path

from mediatracker.core.worker_wake import REASON_LIBRARY_INOTIFY, reset_wake_metrics, wake_counts
from mediatracker.library.inotify_watch import LibraryInotifyWatcher


def test_inotify_debounce_wakes_worker(client, app, settings, monkeypatch):
    del client
    media = Path(settings.data_dir) / "media"
    (media / "Films").mkdir(parents=True, exist_ok=True)
    (media / "Series").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "mediatracker.library.inotify_watch._DEBOUNCE_SECONDS",
        0.2,
    )
    monkeypatch.setattr(
        "mediatracker.library.inotify_watch.is_local_filesystem",
        lambda _path: True,
    )
    monkeypatch.setattr(
        "mediatracker.library.inotify_watch._inotify_init",
        lambda: 99,
    )
    monkeypatch.setattr(
        "mediatracker.library.inotify_watch._inotify_add_watch",
        lambda _fd, _path, _mask: 1,
    )
    monkeypatch.setattr(
        "mediatracker.library.inotify_watch._inotify_rm_watch",
        lambda _fd, _wd: None,
    )
    events = [
        [(1, 0x100, "new.mkv")],
        [],
        [],
    ]

    def read_events(_fd):
        if events:
            return events.pop(0)
        return []

    monkeypatch.setattr("mediatracker.library.inotify_watch._read_events", read_events)
    monkeypatch.setattr("mediatracker.library.inotify_watch.os.read", lambda *_a, **_k: b"")
    monkeypatch.setattr("mediatracker.library.inotify_watch.os.close", lambda _fd: None)
    stop = threading.Event()
    watcher = LibraryInotifyWatcher(settings, app.state.session_factory, stop)
    db = app.state.session_factory()
    try:
        reset_wake_metrics(db)
        db.commit()
    finally:
        db.close()
    watcher.start()
    time.sleep(0.6)
    stop.set()
    time.sleep(0.1)
    db = app.state.session_factory()
    try:
        counts = wake_counts(db)
        assert counts.get(REASON_LIBRARY_INOTIFY, 0) >= 1
    finally:
        db.close()


def test_inotify_init_failure_logs_without_crash(client, app, settings, caplog):
    del client
    stop = threading.Event()
    watcher = LibraryInotifyWatcher(settings, app.state.session_factory, stop)

    def boom():
        raise OSError(1, "no inotify")

    import mediatracker.library.inotify_watch as mod

    original = mod._inotify_init
    mod._inotify_init = boom
    try:
        with caplog.at_level("WARNING", logger="mediatracker.library.inotify_watch"):
            watcher.start()
            time.sleep(0.2)
        stop.set()
        assert any("inotify" in record.message.lower() for record in caplog.records)
    finally:
        mod._inotify_init = original
