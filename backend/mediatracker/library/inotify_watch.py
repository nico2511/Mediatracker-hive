"""inotify on local library roots to wake the worker (debounced)."""

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import logging
import os
import struct
import threading
import time
from pathlib import Path

from mediatracker.config import Settings
from mediatracker.core.local_fs import is_local_filesystem
from mediatracker.core.storage import library_skip, storage_layout
from mediatracker.core.worker_wake import REASON_LIBRARY_INOTIFY, wake_worker
from mediatracker.library.incremental import iter_tracked_folders
from mediatracker.library.scanner import library_roots

logger = logging.getLogger(__name__)

IN_NONBLOCK = 0x00000800
IN_CREATE = 0x00000100
IN_DELETE = 0x00000200
IN_MODIFY = 0x00000002
IN_MOVED_FROM = 0x00000040
IN_MOVED_TO = 0x00000080
IN_CLOSE_WRITE = 0x00000008
IN_ATTRIB = 0x00000004
IN_ISDIR = 0x40000000
IN_MASK = IN_CREATE | IN_DELETE | IN_MODIFY | IN_MOVED_FROM | IN_MOVED_TO | IN_CLOSE_WRITE | IN_ATTRIB

_EVENT_SIZE = struct.calcsize("iIII")
_MAX_WATCHES = 8000
_DEBOUNCE_SECONDS = 5.0


class LibraryInotifyWatcher:
    """Background thread: filesystem hints for the worker on local library trees."""

    def __init__(self, settings: Settings, session_factory, stop: threading.Event) -> None:
        self._settings = settings
        self._factory = session_factory
        self._stop = stop
        self._thread: threading.Thread | None = None
        self._disabled = False
        self._warned = False

    def start(self) -> None:
        if self._disabled:
            return
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="library-inotify", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        try:
            fd = _inotify_init()
        except OSError as exc:
            self._log_fallback(f"inotify_init: {exc}")
            return
        watches: dict[int, Path] = {}
        last_paths: tuple[Path, Path] | None = None
        pending_wake = False
        debounce_until = 0.0
        try:
            while not self._stop.is_set():
                movies, series = self._current_roots()
                if (movies, series) != last_paths:
                    _clear_watches(fd, watches)
                    watches = _register_watches(fd, movies, series, self._factory, self._settings)
                    last_paths = (movies, series)
                if not watches:
                    if self._stop.wait(30.0):
                        break
                    continue
                try:
                    events = _read_events(fd)
                except OSError:
                    if self._stop.wait(1.0):
                        break
                    continue
                if events:
                    pending_wake = True
                    debounce_until = time.monotonic() + _DEBOUNCE_SECONDS
                if pending_wake and time.monotonic() >= debounce_until:
                    pending_wake = False
                    self._wake()
                if not events:
                    wait = min(1.0, max(0.1, debounce_until - time.monotonic())) if pending_wake else 1.0
                    if self._stop.wait(wait):
                        break
        finally:
            os.close(fd)

    def _current_roots(self) -> tuple[Path, Path]:
        db = self._factory()
        try:
            return library_roots(db, self._settings)
        finally:
            db.close()

    def _wake(self) -> None:
        db = self._factory()
        try:
            wake_worker(db, self._settings, REASON_LIBRARY_INOTIFY, commit=True)
        except Exception:
            logger.exception("Réveil worker inotify bibliothèque échoué.")
        finally:
            db.close()

    def _log_fallback(self, detail: str) -> None:
        self._disabled = True
        if not self._warned:
            logger.warning(
                "Surveillance inotify bibliothèque indisponible (%s) : repli sur le scan périodique.",
                detail,
            )
            self._warned = True


def _register_watches(fd: int, movies: Path, series: Path, factory, settings: Settings) -> dict[int, Path]:
    watches: dict[int, Path] = {}
    db = factory()
    try:
        skip = library_skip(storage_layout(db, settings))
    finally:
        db.close()
    for root, kind in ((movies, "film"), (series, "series")):
        if not root.is_dir() or not is_local_filesystem(root):
            continue
        paths = [root.resolve(), *iter_tracked_folders(root, kind, skip)]
        for path in paths:
            if len(watches) >= _MAX_WATCHES:
                logger.warning(
                    "Limite de watches inotify atteinte (%s) : repli sur le scan périodique.",
                    _MAX_WATCHES,
                )
                return watches
            try:
                wd = _inotify_add_watch(fd, str(path), IN_MASK)
            except OSError as exc:
                if exc.errno in {errno.ENOSPC, errno.EMFILE}:
                    logger.warning(
                        "Limite système inotify atteinte (%s) : repli sur le scan périodique.",
                        exc,
                    )
                    return watches
                logger.debug("inotify_add_watch ignoré pour %s (%s)", path, exc)
                continue
            watches[wd] = path
    return watches


def _clear_watches(fd: int, watches: dict[int, Path]) -> None:
    for wd in list(watches):
        try:
            _inotify_rm_watch(fd, wd)
        except OSError:
            pass
    watches.clear()


def _read_events(fd: int) -> list[tuple[int, int, str]]:
    try:
        raw = os.read(fd, 64 * (16 + _EVENT_SIZE))
    except BlockingIOError:
        return []
    except OSError:
        return []
    events: list[tuple[int, int, str]] = []
    offset = 0
    while offset + _EVENT_SIZE <= len(raw):
        wd, mask, _cookie, name_len = struct.unpack_from("iIII", raw, offset)
        offset += _EVENT_SIZE
        name = raw[offset : offset + name_len].split(b"\0", 1)[0].decode(errors="replace")
        offset += name_len
        events.append((wd, mask, name))
    return events


_libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)


def _inotify_init() -> int:
    fd = _libc.inotify_init1(IN_NONBLOCK)
    if fd < 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))
    return fd


def _inotify_add_watch(fd: int, path: str, mask: int) -> int:
    wd = _libc.inotify_add_watch(fd, path.encode(), mask)
    if wd < 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))
    return wd


def _inotify_rm_watch(fd: int, wd: int) -> None:
    if _libc.inotify_rm_watch(fd, wd) < 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))
