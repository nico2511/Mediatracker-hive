"""Correctif 1 — robustesse Actualiser, worker, 500 journalisées, métriques HTTP."""

import logging
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from starlette.applications import Starlette
from starlette.routing import Route
from tests.conftest import csrf, login

from mediatracker.core.aria2_reconcile import reset_startup_reconcile
from mediatracker.core.catalog import catalog_clock, refresh_catalog
from mediatracker.core.metrics.http import HttpMetricsMiddleware, HttpMetricsRegistry
from mediatracker.db.models import Event
from mediatracker.providers.base import ReleaseListing
from mediatracker.worker import _maybe_savepoint, _run_worker_pass

NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
BASE = "https://wawa.test"


class _Feed:
    def __init__(self, rows: dict[str, list[ReleaseListing]]) -> None:
        self.capabilities = frozenset({"latest"})
        self.rows = rows

    def latest_releases(self, media_type: str) -> list[ReleaseListing]:
        return list(self.rows.get(media_type, []))

    def close(self) -> None:
        return None


def _listing(**kwargs) -> ReleaseListing:
    payload = {
        "title": "Titre factice",
        "year": 2021,
        "media_type": "film",
        "quality": "HD",
        "language": "VFF",
        "url": f"{BASE}/film/titre-factice",
        "ref": f"{BASE}/film/titre-factice",
    }
    payload.update(kwargs)
    return ReleaseListing(**payload)


def _db(app):
    return app.state.session_factory()


def test_poster_failure_still_refreshes_catalog(client, app, monkeypatch):
    del client
    feed = _Feed({"film": [_listing()], "series": []})
    db = _db(app)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("panne affiches factice")

    monkeypatch.setattr(
        "mediatracker.core.catalog_poster.resolve_pending_catalog_posters",
        _boom,
    )
    report = refresh_catalog(
        db,
        app.state.settings,
        now=NOW,
        sources=[(1, "wawacity", feed)],
        force=True,
    )
    assert report.added == 1
    last, nxt, error = catalog_clock(db)
    assert last == NOW
    assert nxt is not None and nxt > NOW
    assert error is None or error == ""


def test_refresh_failure_sets_last_error_and_one_catalog_failed(client, app, monkeypatch):
    del client
    feed = _Feed({"film": [_listing()], "series": []})
    db = _db(app)

    monkeypatch.setattr(
        "mediatracker.core.catalog._merge_same_tmdb",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("panne fusion factice")),
    )
    for attempt in range(3):
        refresh_catalog(
            db,
            app.state.settings,
            now=NOW + timedelta(minutes=attempt),
            sources=[(1, "wawacity", feed)],
            force=True,
        )
    _last, nxt, last_error = catalog_clock(db)
    assert last_error and "panne fusion factice" in last_error
    assert nxt is not None
    count = db.scalar(
        select(func.count()).select_from(Event).where(Event.type == "catalog.failed")
    )
    assert count == 1


def test_worker_maybe_continues_after_catalog_failure(client, app):
    del client
    db = _db(app)
    marker = {"purged": False}

    def catalog_boom(_db, _settings):
        raise RuntimeError("catalogue factice")

    def purge_ok(_db):
        marker["purged"] = True

    _maybe_savepoint(db, "Catalogue", lambda: catalog_boom(db, app.state.settings))
    _maybe_savepoint(db, "Purge événements", lambda: purge_ok(db))
    db.commit()
    assert marker["purged"] is True


def test_unhandled_route_returns_problem_and_system_error(app):
    def _boom(*_args, **_kwargs):
        raise RuntimeError("panne route factice")

    app.state.metrics.snapshot = _boom
    with TestClient(app, raise_server_exceptions=False) as client:
        login(client)
        response = client.get("/api/v1/health/metrics", headers=csrf(client))
        assert response.status_code == 500
        assert response.headers["content-type"].startswith("application/problem+json")
        body = response.json()
        assert body["status"] == 500
        correlation_id = body.get("correlation_id")
        assert isinstance(correlation_id, str) and correlation_id
        assert correlation_id in body.get("detail", "")

    check = _db(app)
    try:
        row = check.scalar(
            select(Event)
            .where(Event.type == "system.error")
            .order_by(Event.id.desc())
            .limit(1)
        )
        assert row is not None
        assert row.payload.get("correlation_id") == correlation_id
        assert row.payload.get("route") == "/api/v1/health/metrics"
        assert row.payload.get("exception_type") == "RuntimeError"
    finally:
        check.close()


def test_http_middleware_records_failed_route():
    registry = HttpMetricsRegistry()

    async def boom(_request):
        raise RuntimeError("panne middleware factice")

    app = Starlette(routes=[Route("/boom", boom)])
    app.add_middleware(HttpMetricsMiddleware, registry=registry)
    client = TestClient(app, raise_server_exceptions=False)
    client.get("/boom")
    snap = registry.snapshot()
    assert snap["/boom"]["count"] == 1
    assert snap["/boom"]["mean_ms"] is not None


def test_run_worker_pass_without_logged_step_failures(client, app, caplog):
    del client
    reset_startup_reconcile()
    db = _db(app)
    try:
        with caplog.at_level(logging.ERROR, logger="mediatracker.worker"):
            _run_worker_pass(db, app.state.settings, app.state.session_factory)
            db.commit()
    finally:
        db.close()
    failures = [record.message for record in caplog.records if "a échoué ; le passage continue." in record.message]
    assert failures == []
