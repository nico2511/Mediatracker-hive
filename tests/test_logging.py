"""Logging stays one JSON line per event, even if configuration runs again."""

import io
import json
import logging
import sys
import types
from logging.config import dictConfig
from pathlib import Path

import uvicorn

from mediatracker.security.logging import configure_logging

# The text shape reported from production. It is not selected by any setting
# in this repository. Four passes, each writing the rendered line back into
# the record, are what produce the repeated INFO prefix and the extra " | ".
_TEXT_FORMAT = "%(levelname)s %(message)s | ts=%(asctime)s logger=%(name)s"
_LEGACY_INI = """\
[loggers]
keys = root,uvicorn,alembic

[handlers]
keys = h1,h2,h3,h4

[formatters]
keys = text

[logger_root]
level = INFO
handlers = h1,h2,h3,h4

[logger_uvicorn]
level = INFO
handlers = h1
qualname = uvicorn.access
propagate = 0

[logger_alembic]
level = INFO
handlers = h2
qualname = alembic
propagate = 0

[handler_h1]
class = StreamHandler
level = NOTSET
formatter = text
args = (sys.stderr,)

[handler_h2]
class = StreamHandler
level = NOTSET
formatter = text
args = (sys.stderr,)

[handler_h3]
class = StreamHandler
level = NOTSET
formatter = text
args = (sys.stderr,)

[handler_h4]
class = StreamHandler
level = NOTSET
formatter = text
args = (sys.stderr,)

[formatter_text]
format = %%(levelname)s %%(message)s | ts=%%(asctime)s logger=%%(name)s
"""

_LOG_CONFIG = Path(__file__).resolve().parents[1] / "docker" / "log-config.json"

_ADOPTED = ("uvicorn", "uvicorn.error", "uvicorn.access", "uvicorn.asgi", "alembic")


def test_a41_configure_logging_idempotent(tmp_path):
    """A41 : un second appel de configure_logging ne rajoute aucun handler."""
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    try:
        configure_logging("INFO", tmp_path)
        handlers_after_first = list(root.handlers)
        assert len(_stream_handlers(root)) == 1

        configure_logging("INFO", tmp_path)
        assert list(root.handlers) == handlers_after_first

        stream = _stream_handlers(root)[0]
        buf = io.StringIO()
        stream.setStream(buf)
        logging.getLogger("mediatracker.worker").info("événement unique")
        lines = [line for line in buf.getvalue().splitlines() if line.strip()]
        assert len(lines) == 1
        payload = json.loads(lines[0])
        assert payload["message"] == "événement unique"
        assert lines[0].count("INFO") == 1
    finally:
        for handler in list(root.handlers):
            if handler not in saved_handlers:
                root.removeHandler(handler)
                handler.close()
        root.handlers = saved_handlers
        root.setLevel(saved_level)


def _stream_handlers(root: logging.Logger) -> list[logging.Handler]:
    return [
        handler
        for handler in root.handlers
        if type(handler) is logging.StreamHandler and not getattr(handler, "_mediatracker_file", False)
    ]


def test_configure_logging_does_not_double(tmp_path):
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    saved_filters = list(root.filters)
    saved_loggers = {}
    for name in _ADOPTED:
        logger = logging.getLogger(name)
        saved_loggers[name] = (list(logger.handlers), logger.propagate, logger.level)
    try:
        # Same order as `python -m uvicorn`: its dictConfig, then the app.
        dictConfig(uvicorn.config.LOGGING_CONFIG)
        configure_logging("INFO", tmp_path)
        configure_logging("INFO", tmp_path)
        configure_logging("INFO", tmp_path)

        import logging.config as logging_config

        # Uvicorn configures again after the app (reload, extra workers).
        logging_config.dictConfig(uvicorn.config.LOGGING_CONFIG)

        stream = next(handler for handler in root.handlers if type(handler) is logging.StreamHandler)
        buf = io.StringIO()
        stream.setStream(buf)
        logging.getLogger("mediatracker.bootstrap").info("démarrage unique")
        logging.getLogger("uvicorn.error").info("jeton mt_prefix_secretvalue")
        logging.getLogger("uvicorn.access").info(
            '%s - "%s %s HTTP/%s" %d',
            "127.0.0.1:1",
            "GET",
            "/api/v1/media/13/jellyfin",
            "1.1",
            200,
        )

        lines = [line for line in buf.getvalue().splitlines() if line.strip()]
        assert len(lines) == 3
        first = json.loads(lines[0])
        second = json.loads(lines[1])
        third = json.loads(lines[2])
        assert first == {
            "ts": first["ts"],
            "level": "INFO",
            "logger": "mediatracker.bootstrap",
            "message": "démarrage unique",
        }
        assert first["ts"].endswith("Z")
        assert lines[0].count("INFO") == 1
        assert lines[0].count("démarrage unique") == 1
        assert second["level"] == "INFO"
        assert second["logger"] == "uvicorn.error"
        assert second["message"] == "jeton mt_[redacted]"
        assert "secretvalue" not in lines[1]
        assert lines[1].count("INFO") == 1
        assert third["logger"] == "uvicorn.access"
        assert "GET /api/v1/media/13/jellyfin" in third["message"]
        assert "|" not in lines[2]
        assert lines[2].count("INFO") == 1
        for line in lines:
            assert line.count("INFO") == 1
            assert "|" not in line

        file_lines = (tmp_path / "mediatracker.log").read_text(encoding="utf-8").splitlines()
        assert file_lines == lines
        ours = [handler for handler in root.handlers if getattr(handler, "_mediatracker", False)]
        assert len(ours) == 2
        assert sum(1 for handler in ours if getattr(handler, "_mediatracker_file", False)) == 1
    finally:
        for handler in list(root.handlers):
            if handler not in saved_handlers:
                root.removeHandler(handler)
                handler.close()
        root.handlers = saved_handlers
        root.setLevel(saved_level)
        root.filters = saved_filters
        for name, (handlers, propagate, level) in saved_loggers.items():
            logger = logging.getLogger(name)
            logger.handlers = handlers
            logger.propagate = propagate
            logger.setLevel(level)


def test_container_entrypoint_logs_each_line_once(tmp_path):
    """Same order as s6: uvicorn --log-config, then the app configures logging."""
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    saved_filters = list(root.filters)
    saved_loggers = {}
    for name in _ADOPTED:
        logger = logging.getLogger(name)
        saved_loggers[name] = (list(logger.handlers), logger.propagate, logger.level)
    try:
        dictConfig(json.loads(_LOG_CONFIG.read_text(encoding="utf-8")))
        configure_logging("INFO", tmp_path)
        # Uvicorn looks up dictConfig again if it reloads. The guard must not stack handlers.
        import logging.config as logging_config

        logging_config.dictConfig(json.loads(_LOG_CONFIG.read_text(encoding="utf-8")))

        stream = next(handler for handler in root.handlers if type(handler) is logging.StreamHandler)
        buf = io.StringIO()
        stream.setStream(buf)
        logging.getLogger("mediatracker.core.pipeline").info("Aimant Comet écarté (DISCONTINUED).")
        logging.getLogger("uvicorn.access").info(
            '%s - "%s %s HTTP/%s" %d',
            "10.0.0.8:9",
            "POST",
            "/api/v1/media",
            "1.1",
            503,
        )
        logging.getLogger("uvicorn.error").info("démarrage")

        lines = [line for line in buf.getvalue().splitlines() if line.strip()]
        assert len(lines) == 3
        assert [json.loads(line)["logger"] for line in lines] == [
            "mediatracker.core.pipeline",
            "uvicorn.access",
            "uvicorn.error",
        ]
        assert logging.getLogger("uvicorn.access").handlers == []
        assert logging.getLogger("uvicorn").handlers == []
        assert sum(1 for handler in root.handlers if type(handler) is logging.StreamHandler) == 1
        file_lines = (tmp_path / "mediatracker.log").read_text(encoding="utf-8").splitlines()
        assert file_lines == lines
    finally:
        for handler in list(root.handlers):
            if handler not in saved_handlers:
                root.removeHandler(handler)
                handler.close()
        root.handlers = saved_handlers
        root.setLevel(saved_level)
        root.filters = saved_filters
        for name, (handlers, propagate, level) in saved_loggers.items():
            logger = logging.getLogger(name)
            logger.handlers = handlers
            logger.propagate = propagate
            logger.setLevel(level)


class _DerivedStreamHandler(logging.StreamHandler):
    """Some stacks register StreamHandler subclasses instead of the base class."""


def test_stacked_stderr_handlers_emit_one_line(tmp_path):
    """Four stderr handlers must not print INFO four times on one record."""
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    try:
        configure_logging("INFO", tmp_path)
        for _ in range(3):
            handler = _DerivedStreamHandler()
            handler.setFormatter(logging.Formatter("%(levelname)s %(message)s | ts=%(asctime)s logger=%(name)s"))
            root.addHandler(handler)
        buf = io.StringIO()
        for handler in root.handlers:
            if type(handler) is logging.StreamHandler:
                handler.setStream(buf)
        logging.getLogger("mediatracker.worker").info("Worker démarré")
        lines = [line for line in buf.getvalue().splitlines() if line.strip()]
        assert len(lines) == 1
        assert lines[0].count("INFO") == 1
        assert "INFO INFO" not in lines[0]
        assert "|" not in lines[0]
        assert json.loads(lines[0])["message"] == "Worker démarré"
        file_lines = (tmp_path / "mediatracker.log").read_text(encoding="utf-8").splitlines()
        assert len(file_lines) == 1
    finally:
        for handler in list(root.handlers):
            if handler not in saved_handlers:
                root.removeHandler(handler)
                handler.close()
        root.handlers = saved_handlers
        root.setLevel(saved_level)


def _stdlib(fn):
    while getattr(fn, "__wrapped__", None) is not None:
        fn = fn.__wrapped__
    return fn


def test_four_passes_of_the_text_formatter_nest_the_level():
    """The production line is this formatter run four times on one record.

    Each pass stores its rendering in record.msg. The next pass then prefixes
    another INFO and appends another " | ts=… logger=…".
    """
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, "msg", (), None)
    formatter = logging.Formatter(_TEXT_FORMAT)
    rendered = ""
    for _ in range(4):
        rendered = formatter.format(record)
        record.msg = rendered
        record.args = None
    assert rendered.startswith("INFO INFO INFO INFO msg |")
    assert rendered.count(" | ts=") == 4
    assert rendered.endswith("logger=uvicorn.access")
    assert rendered.count("INFO") == 4


def test_legacy_env_and_config_file_stay_json(tmp_path, monkeypatch):
    """A text format from the environment or from /config never wins.

    LOG_FORMAT, LOGGING_CONFIG, UVICORN_LOG_CONFIG, LOG_CONFIG, and S6_LOGGING
    are not format selectors. A logging.ini / logging.yaml / logging.json left
    in the config directory is not loaded. A dictConfig or fileConfig that
    already ran — including one imported before the guard — is replaced.
    """
    config_dir = tmp_path / "config"
    log_dir = config_dir / "logs"
    log_dir.mkdir(parents=True)
    ini = config_dir / "logging.ini"
    ini.write_text(_LEGACY_INI, encoding="utf-8")
    yaml_path = config_dir / "logging.yaml"
    yaml_path.write_text("format: " + _TEXT_FORMAT + "\n", encoding="utf-8")
    (config_dir / "logging.json").write_text("{}\n", encoding="utf-8")
    (config_dir / "log-config.json").write_text("{}\n", encoding="utf-8")
    legacy = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"text": {"format": _TEXT_FORMAT}},
        "handlers": {
            name: {"class": "logging.StreamHandler", "formatter": "text"}
            for name in ("h1", "h2", "h3", "h4")
        },
        "loggers": {
            "uvicorn.access": {"handlers": ["h1"], "level": "INFO", "propagate": False},
            "alembic": {"handlers": ["h2"], "level": "INFO", "propagate": False},
        },
        "root": {"handlers": ["h1", "h2", "h3", "h4"], "level": "INFO"},
    }
    monkeypatch.setenv("LOG_FORMAT", _TEXT_FORMAT)
    monkeypatch.setenv("LOGGING_CONFIG", str(yaml_path))
    monkeypatch.setenv("UVICORN_LOG_CONFIG", str(ini))
    monkeypatch.setenv("LOG_CONFIG", str(ini))
    monkeypatch.setenv("S6_LOGGING", "1")

    import logging.config as logging_config

    real_dict = _stdlib(logging_config.dictConfig)
    real_file = _stdlib(logging_config.fileConfig)
    early = types.ModuleType("mediatracker_legacy_logging_binding")
    early.dictConfig = real_dict
    early.fileConfig = real_file
    sys.modules[early.__name__] = early

    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    saved_filters = list(root.filters)
    saved_loggers = {}
    for name in _ADOPTED:
        logger = logging.getLogger(name)
        saved_loggers[name] = (list(logger.handlers), logger.propagate, logger.level, logger.disabled)
    try:
        real_file(str(ini))
        real_dict(legacy)
        configure_logging("info", log_dir)
        assert early.dictConfig is not real_dict
        assert early.fileConfig is not real_file
        early.dictConfig(legacy)
        early.fileConfig(str(ini))

        assert logging.getLogger("uvicorn").handlers == []
        assert logging.getLogger("uvicorn.access").handlers == []
        assert logging.getLogger("uvicorn.access").propagate is True
        assert logging.getLogger("alembic").handlers == []
        assert logging.getLogger("alembic").propagate is True
        assert sum(1 for handler in root.handlers if type(handler) is logging.StreamHandler) == 1

        stream = next(handler for handler in root.handlers if type(handler) is logging.StreamHandler)
        buf = io.StringIO()
        stream.setStream(buf)
        logging.getLogger("uvicorn.access").info("page d'accueil")
        lines = [line for line in buf.getvalue().splitlines() if line.strip()]
        assert len(lines) == 1
        payload = json.loads(lines[0])
        assert payload["logger"] == "uvicorn.access"
        assert payload["level"] == "INFO"
        assert payload["message"] == "page d'accueil"
        assert "INFO INFO" not in lines[0]
        assert "|" not in lines[0]

        file_text = (log_dir / "mediatracker.log").read_text(encoding="utf-8")
        assert "LOG_FORMAT" in file_text
        assert "S6_LOGGING=1" in file_text
        assert "logging.yaml" in file_text
        assert "logging.ini" in file_text
        assert "page d'accueil" in file_text
        assert "INFO INFO" not in file_text
        assert _TEXT_FORMAT not in file_text
    finally:
        sys.modules.pop(early.__name__, None)
        for handler in list(root.handlers):
            if handler not in saved_handlers:
                root.removeHandler(handler)
                handler.close()
        root.handlers = saved_handlers
        root.setLevel(saved_level)
        root.filters = saved_filters
        for name, (handlers, propagate, level, disabled) in saved_loggers.items():
            logger = logging.getLogger(name)
            logger.handlers = handlers
            logger.propagate = propagate
            logger.setLevel(level)
            logger.disabled = disabled
