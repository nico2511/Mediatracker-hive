"""Redact API keys, bearer credentials, and personal tokens from log records.

Logs are JSON, on stderr and in a rotating file under the config directory.
"""

import json
import logging
import logging.config
import logging.handlers
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_BEARER = re.compile(r"(Bearer\s+)\S+", re.IGNORECASE)
_API_TOKEN = re.compile(r"\bmt_[A-Za-z0-9]+_[A-Za-z0-9_\-]+\b")
_GH_TOKEN = re.compile(r"\b(?:ghp_|github_pat_)[A-Za-z0-9_]+")
_API_KEY = re.compile(
    r"((?:api[_-]?key|authorization|rpc-secret|token)[\"']?\s*[:=]\s*[\"']?)[^\s&\"']+",
    re.IGNORECASE,
)
_QUERY_TOKEN = re.compile(r"([?&]token=)[^&\s\"']+", re.IGNORECASE)


def redact(value: str) -> str:
    value = _QUERY_TOKEN.sub(r"\1[redacted]", value)
    value = _BEARER.sub(r"\1[redacted]", value)
    value = _GH_TOKEN.sub("[redacted]", value)
    value = _API_TOKEN.sub("mt_[redacted]", value)
    value = _API_KEY.sub(r"\1[redacted]", value)
    return value


class SecretRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact(arg) if isinstance(arg, str) else arg for arg in record.args)
        elif isinstance(record.args, dict):
            record.args = {
                key: redact(arg) if isinstance(arg, str) else arg
                for key, arg in record.args.items()
            }
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = redact(record.getMessage())
        payload: dict[str, str] = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
        }
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


class _QuietRotatingFileHandler(logging.handlers.RotatingFileHandler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            super().emit(record)
        except OSError:
            return


_ORIGINAL_STREAM_EMIT = logging.StreamHandler.emit
_STREAM_PATCHED = False


def _emit_stderr_once(self, record: logging.LogRecord) -> None:
    """One stderr line per record, even when several StreamHandlers are stacked.

    File handlers are subclasses and still write. s6 and Uvicorn have both
    been seen adding a second stderr handler; the record flag drops the copies.
    Pytest ``caplog`` also subclasses ``StreamHandler``; it must still emit so
    records get a formatted ``message`` attribute.
    """
    if getattr(self, "_mediatracker_file", False) or not isinstance(self, logging.StreamHandler):
        _ORIGINAL_STREAM_EMIT(self, record)
        return
    if type(self).__module__.startswith("_pytest."):
        _ORIGINAL_STREAM_EMIT(self, record)
        return
    if getattr(record, "_mediatracker_stderr", False):
        return
    record._mediatracker_stderr = True  # type: ignore[attr-defined]
    _ORIGINAL_STREAM_EMIT(self, record)


def _patch_stream_emit() -> None:
    global _STREAM_PATCHED
    if _STREAM_PATCHED:
        return
    logging.StreamHandler.emit = _emit_stderr_once  # type: ignore[method-assign]
    _STREAM_PATCHED = True


_LEVEL = "INFO"
_LOG_DIR: Path | None = None
_LOG_MAX_BYTES = 2_000_000
_LOG_BACKUP_COUNT = 5
_ORIGINAL_DICT = None
_ORIGINAL_FILE = None
_ORIGINAL_BASIC = None

# Nothing in this process reads these. A frozen compose or a file dropped in
# /config can still be fed to Uvicorn's --log-config or to Alembic's fileConfig.
# configure_logging reports them and then installs JSON anyway.
_LEGACY_ENV = ("LOG_FORMAT", "LOGGING_CONFIG", "UVICORN_LOG_CONFIG", "LOG_CONFIG")
_LEGACY_FILES = (
    "logging.yaml",
    "logging.yml",
    "logging.json",
    "logging.ini",
    "logging.conf",
    "log-config.json",
)
# Parents of these names keep propagate=True and no handler of their own.
_ADOPTED = ("uvicorn", "uvicorn.error", "uvicorn.access", "uvicorn.asgi", "alembic")
_QUIET = {"httpx": logging.WARNING, "httpcore": logging.WARNING}

# uvicorn.run turns ``log_config=None`` into its default LOGGING_CONFIG (text handlers
# on uvicorn.*). Pass this mapping instead so startup does not stack extra handlers.
UVICORN_LOG_CONFIG_SKIP: dict[str, int | bool] = {"version": 1, "disable_existing_loggers": False}

def _normalize_level(level: str) -> str:
    text = str(level or "INFO").strip().upper()
    try:
        logging._checkLevel(text)
    except (TypeError, ValueError):
        return "INFO"
    return text


def _legacy_overrides(log_dir: Path | None) -> list[str]:
    found = [name for name in _LEGACY_ENV if os.environ.get(name, "").strip()]
    s6 = os.environ.get("S6_LOGGING", "").strip()
    if s6 and s6 != "0":
        found.append(f"S6_LOGGING={s6}")
    if log_dir is None:
        return found
    roots = [log_dir]
    if log_dir.parent != log_dir:
        roots.append(log_dir.parent)
    for root in roots:
        for name in _LEGACY_FILES:
            path = root / name
            if path.is_file():
                found.append(str(path))
    return found


def _dict_config(config: dict) -> None:
    """Stdlib dictConfig, never the wrapper installed below."""
    fn = _ORIGINAL_DICT or logging.config.dictConfig
    fn(config)


def _revive(level: str) -> None:
    """dictConfig(disable_existing_loggers=True) disables loggers it does not name.

    Application loggers must keep emitting, through the root handler only.
    """
    root = logging.getLogger()
    root.disabled = False
    root.setLevel(level)
    root.filters = [existing for existing in root.filters if not isinstance(existing, SecretRedactionFilter)]
    for name, candidate in list(logging.Logger.manager.loggerDict.items()):
        if not isinstance(candidate, logging.Logger):
            continue
        candidate.disabled = False
        candidate.propagate = True
        candidate.filters = [
            existing for existing in candidate.filters if not isinstance(existing, SecretRedactionFilter)
        ]
        for handler in list(candidate.handlers):
            candidate.removeHandler(handler)
            if getattr(handler, "_mediatracker_file", False):
                handler.close()
        if name in _QUIET:
            candidate.setLevel(_QUIET[name])


def _stream_handlers() -> list[logging.Handler]:
    root = logging.getLogger()
    return [
        handler
        for handler in root.handlers
        if isinstance(handler, logging.StreamHandler) and not getattr(handler, "_mediatracker_file", False)
    ]


def _file_handlers() -> list[logging.Handler]:
    root = logging.getLogger()
    return [handler for handler in root.handlers if getattr(handler, "_mediatracker_file", False)]


def _layout_matches(log_dir: Path | None) -> bool:
    streams = _stream_handlers()
    if len(streams) != 1 or not getattr(streams[0], "_mediatracker", False):
        return False
    if not isinstance(streams[0].formatter, JsonFormatter):
        return False
    files = _file_handlers()
    if log_dir is None:
        return not files
    return len(files) == 1 and isinstance(files[0].formatter, JsonFormatter)


def _adopted_ok() -> bool:
    for name in _ADOPTED:
        logger = logging.getLogger(name)
        if logger.handlers:
            return False
        if not logger.propagate:
            return False
    return True


def _already_installed(level: str, log_dir: Path | None) -> bool:
    root = logging.getLogger()
    try:
        expected = logging._checkLevel(level)
    except (TypeError, ValueError):
        return False
    return root.level == expected and _layout_matches(log_dir) and _adopted_ok()


def _strip_root_handlers() -> None:
    """Drop every handler on root (legacy ini / log-config may stack several stderr sinks)."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except OSError:
            pass


def _install(level: str, log_dir: Path | None, *, max_bytes: int, backup_count: int) -> None:
    """One stderr handler and, when asked, one rotating file. Nothing else emits."""
    _strip_root_handlers()
    _dict_config(
        {
            "version": 1,
            "disable_existing_loggers": True,
            "formatters": {"json": {"()": JsonFormatter}},
            "handlers": {
                "stderr": {
                    "class": "logging.StreamHandler",
                    "formatter": "json",
                    "stream": "ext://sys.stderr",
                }
            },
            "root": {"handlers": ["stderr"], "level": level},
            "loggers": {
                **{
                    name: {"handlers": [], "level": "INFO", "propagate": True}
                    for name in _ADOPTED
                },
                "httpx": {"handlers": [], "level": "WARNING", "propagate": True},
                "httpcore": {"handlers": [], "level": "WARNING", "propagate": True},
            },
        }
    )
    root = logging.getLogger()
    _revive(level)
    for handler in root.handlers:
        if isinstance(handler, logging.StreamHandler) and not getattr(handler, "_mediatracker_file", False):
            handler._mediatracker = True  # type: ignore[attr-defined]
    if log_dir is None:
        return
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = _QuietRotatingFileHandler(
            log_dir / "mediatracker.log",
            maxBytes=max(10_000, int(max_bytes)),
            backupCount=max(1, int(backup_count)),
            encoding="utf-8",
        )
    except OSError:
        return
    file_handler.setFormatter(JsonFormatter())
    file_handler._mediatracker = True  # type: ignore[attr-defined]
    file_handler._mediatracker_file = True  # type: ignore[attr-defined]
    for extra in [h for h in root.handlers if getattr(h, "_mediatracker_file", False)]:
        root.removeHandler(extra)
        extra.close()
    root.addHandler(file_handler)
    streams = [
        h
        for h in root.handlers
        if isinstance(h, logging.StreamHandler) and not getattr(h, "_mediatracker_file", False)
    ]
    for duplicate in streams[1:]:
        root.removeHandler(duplicate)
        duplicate.close()


def _rebind(name: str, original, wrapped) -> None:
    if original is None:
        return
    for module in list(sys.modules.values()):
        if module is None:
            continue
        try:
            current = getattr(module, name)
        except Exception:
            continue
        if current is original:
            try:
                setattr(module, name, wrapped)
            except Exception:
                continue


def _guard_reconfigure() -> None:
    """A later dictConfig, fileConfig, or basicConfig must not put text handlers back.

    ``from logging.config import dictConfig`` keeps the stdlib function. Those
    bindings are rewritten too, including ones taken before this guard existed.
    """
    global _ORIGINAL_DICT, _ORIGINAL_FILE, _ORIGINAL_BASIC

    if not getattr(logging.config.dictConfig, "_mediatracker_guard", False):
        _ORIGINAL_DICT = logging.config.dictConfig
        _ORIGINAL_FILE = logging.config.fileConfig
        _ORIGINAL_BASIC = logging.basicConfig

        def dict_config(*args, **kwargs):
            _ORIGINAL_DICT(*args, **kwargs)
            _install(_LEVEL, _LOG_DIR, max_bytes=_LOG_MAX_BYTES, backup_count=_LOG_BACKUP_COUNT)

        def file_config(*args, **kwargs):
            _ORIGINAL_FILE(*args, **kwargs)
            _install(_LEVEL, _LOG_DIR, max_bytes=_LOG_MAX_BYTES, backup_count=_LOG_BACKUP_COUNT)

        def basic_config(*args, **kwargs):
            _ORIGINAL_BASIC(*args, **kwargs)
            _install(_LEVEL, _LOG_DIR, max_bytes=_LOG_MAX_BYTES, backup_count=_LOG_BACKUP_COUNT)

        dict_config._mediatracker_guard = True  # type: ignore[attr-defined]
        dict_config.__wrapped__ = _ORIGINAL_DICT  # type: ignore[attr-defined]
        file_config._mediatracker_guard = True  # type: ignore[attr-defined]
        file_config.__wrapped__ = _ORIGINAL_FILE  # type: ignore[attr-defined]
        basic_config._mediatracker_guard = True  # type: ignore[attr-defined]
        basic_config.__wrapped__ = _ORIGINAL_BASIC  # type: ignore[attr-defined]
        logging.config.dictConfig = dict_config  # type: ignore[assignment]
        logging.config.fileConfig = file_config  # type: ignore[assignment]
        logging.basicConfig = basic_config  # type: ignore[method-assign]

    _rebind("dictConfig", _ORIGINAL_DICT, logging.config.dictConfig)
    _rebind("fileConfig", _ORIGINAL_FILE, logging.config.fileConfig)
    _rebind("basicConfig", _ORIGINAL_BASIC, logging.basicConfig)


def configure_logging(
    level: str = "INFO",
    log_dir: Path | None = None,
    *,
    max_bytes: int | None = None,
    backup_count: int | None = None,
) -> None:
    """One JSON line per event, on stderr and optionally in the rotating file.

    Safe to call more than once. Env vars and files under the config directory
    do not select a format. A later ``dictConfig``, ``fileConfig``, or
    ``basicConfig`` is stripped again, including a copy imported earlier.
    """
    global _LEVEL, _LOG_DIR, _LOG_MAX_BYTES, _LOG_BACKUP_COUNT
    _LEVEL = _normalize_level(level)
    _LOG_DIR = log_dir
    if max_bytes is not None:
        _LOG_MAX_BYTES = max(10_000, int(max_bytes))
    if backup_count is not None:
        _LOG_BACKUP_COUNT = max(1, int(backup_count))
    ignored = _legacy_overrides(log_dir)
    _patch_stream_emit()
    _guard_reconfigure()
    if _already_installed(_LEVEL, _LOG_DIR):
        _revive(_LEVEL)
    else:
        _install(_LEVEL, _LOG_DIR, max_bytes=_LOG_MAX_BYTES, backup_count=_LOG_BACKUP_COUNT)
    if ignored:
        logging.getLogger("mediatracker.logging").warning(
            "Réglage de journal ignoré (%s). Une seule ligne JSON est écrite.",
            ", ".join(ignored),
        )
