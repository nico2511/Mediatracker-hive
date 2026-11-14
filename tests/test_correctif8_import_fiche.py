"""Correctif 8 — import async, session expirée, journal system.error, logs."""

from __future__ import annotations

import io
import json
import logging
import time

import pytest
from sqlalchemy import select
from tests.conftest import csrf, login
from tests.test_jellyfin_history_db_session import USER_ID, _create_efcore_db, _upload_session

from mediatracker.db.models import Event
from mediatracker.events.text import notification_text
from mediatracker.integrations.jellyfin_history_jobs import reset_jobs, wait_job


def _auth(client):
    login(client)
    return csrf(client)


def test_db_preview_async_job_returns_progress_and_result(client, app, tmp_path):
    reset_jobs()
    jf = tmp_path / "jellyfin.db"
    _create_efcore_db(jf)
    session_id, _ = _upload_session(client, app, jellyfin=jf)
    headers = _auth(client)
    started = client.post(
        f"/api/v1/library/jellyfin-history/db/session/{session_id}/preview",
        headers=headers,
        json={"user_id": USER_ID},
    )
    assert started.status_code == 202, started.text
    job_id = started.json()["job_id"]
    deadline = time.monotonic() + 30.0
    last_percent = -1
    while time.monotonic() < deadline:
        status = client.get(f"/api/v1/library/jellyfin-history/jobs/{job_id}", headers=headers)
        assert status.status_code == 200
        body = status.json()
        assert body["status"] in {"running", "complete", "error"}
        assert body["progress"]["percent"] >= last_percent
        last_percent = body["progress"]["percent"]
        if body["status"] == "complete":
            assert body["result"]["line_count"] == 2
            return
        if body["status"] == "error":
            pytest.fail(body.get("error"))
        time.sleep(0.05)
    pytest.fail("preview job did not complete in time")


def test_import_session_unknown_returns_410_with_french_detail(client, app):
    headers = _auth(client)
    missing = "aaaaaaaaaaaaaaaaaaaaaaaa"
    response = client.get(
        f"/api/v1/library/jellyfin-history/db/session/{missing}/users",
        headers=headers,
    )
    assert response.status_code == 410
    body = response.json()
    assert "expirée" in body["detail"].lower()
    assert "renvoyez" in body["detail"].lower()


def test_system_error_journal_includes_readable_message():
    title, detail = notification_text(
        "system.error",
        {
            "route": "/api/v1/test",
            "method": "GET",
            "exception_type": "RuntimeError",
            "exception_message": "panne factice sans secret",
            "correlation_id": "abc123",
        },
    )
    assert "RuntimeError" in detail
    assert "panne factice" in detail
    assert title == "Erreur serveur"


def test_import_job_failure_is_journalized(client, app, tmp_path, monkeypatch):
    reset_jobs()
    jf = tmp_path / "jellyfin.db"
    _create_efcore_db(jf)
    session_id, _ = _upload_session(client, app, jellyfin=jf)
    headers = _auth(client)

    def boom(*_args, **_kwargs):
        raise RuntimeError("export factice en échec")

    monkeypatch.setattr(
        "mediatracker.api.v1.jellyfin_history.export_history_for_user_id_with_stats",
        boom,
    )
    started = client.post(
        f"/api/v1/library/jellyfin-history/db/session/{session_id}/preview",
        headers=headers,
        json={"user_id": USER_ID},
    )
    assert started.status_code == 202
    job_id = started.json()["job_id"]
    row = wait_job(job_id, timeout=10.0)
    assert row["status"] == "error"

    db = app.state.session_factory()
    try:
        event = db.scalar(
            select(Event)
            .where(Event.type == "jellyfin_history.import_failed")
            .order_by(Event.id.desc())
            .limit(1)
        )
        assert event is not None
        assert event.payload.get("exception_type") == "RuntimeError"
        assert "factice" in str(event.payload.get("message", ""))
    finally:
        db.close()


def test_docker_entrypoint_logging_one_json_line(tmp_path):
    """Point 4 : chaîne conteneur (log-config, serve, app, worker) sans empilement ni INFO×4."""
    import logging.config as logging_config
    from logging.config import dictConfig
    from pathlib import Path

    from mediatracker.security.logging import JsonFormatter, configure_logging

    log_config_path = Path(__file__).resolve().parents[1] / "docker" / "log-config.json"
    legacy_text = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"text": {"format": "%(levelname)s %(message)s | ts=%(asctime)s logger=%(name)s"}},
        "handlers": {f"h{i}": {"class": "logging.StreamHandler", "formatter": "text"} for i in range(1, 5)},
        "root": {"handlers": ["h1", "h2", "h3", "h4"], "level": "INFO"},
    }
    real_dict = getattr(logging_config.dictConfig, "__wrapped__", logging_config.dictConfig)

    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    try:
        dictConfig(json.loads(log_config_path.read_text(encoding="utf-8")))
        configure_logging("INFO", tmp_path)
        dictConfig(json.loads(log_config_path.read_text(encoding="utf-8")))
        configure_logging("INFO", tmp_path)
        configure_logging("INFO", tmp_path)
        real_dict(legacy_text)

        configure_logging("INFO", tmp_path)

        streams = [
            handler
            for handler in root.handlers
            if type(handler) is logging.StreamHandler and not getattr(handler, "_mediatracker_file", False)
        ]
        files = [handler for handler in root.handlers if getattr(handler, "_mediatracker_file", False)]
        assert len(streams) == 1
        assert isinstance(streams[0].formatter, JsonFormatter)
        assert len(files) == 1

        buf = io.StringIO()
        streams[0].setStream(buf)
        logging.getLogger("mediatracker.core.pipeline").info("Import Jellyfin terminé.")
        logging.getLogger("uvicorn.access").info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:9", "GET", "/health", "1.1", 200)

        stderr_lines = [line for line in buf.getvalue().splitlines() if line.strip()]
        assert len(stderr_lines) == 2
        for line in stderr_lines:
            assert line.count("INFO") == 1
            assert "INFO INFO" not in line
            assert "| ts=" not in line
            json.loads(line)

        file_lines = (tmp_path / "mediatracker.log").read_text(encoding="utf-8").splitlines()
        assert file_lines == stderr_lines
        assert file_lines[0].count("Import Jellyfin terminé.") == 1
    finally:
        for handler in list(root.handlers):
            if handler not in saved_handlers:
                root.removeHandler(handler)
                handler.close()
        root.handlers = saved_handlers
        root.setLevel(saved_level)
