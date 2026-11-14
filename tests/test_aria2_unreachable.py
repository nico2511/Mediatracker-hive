"""aria2 UNREACHABLE: journal after threshold, dedupe, recovery."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from tests.conftest import login
from tests.test_pipeline import FakeDebrid, FakeDownloader, _enable_debrid, _link, _series

from mediatracker.core.pipeline import tick
from mediatracker.db.models import Download, Event, Setting
from mediatracker.providers.downloaders.aria2 import Aria2Error


def _offline(_handle: str):
    raise Aria2Error("UNREACHABLE", "aria2 est injoignable.")


def _set_threshold(app, minutes: int) -> None:
    db = app.state.session_factory()
    try:
        row = db.get(Setting, "aria2_unreachable_alert_min")
        if row is None:
            db.add(
                Setting(
                    key="aria2_unreachable_alert_min",
                    value=str(minutes),
                    is_secret=False,
                    updated_at=datetime.now(timezone.utc),
                )
            )
        else:
            row.value = str(minutes)
        db.commit()
    finally:
        db.close()


def _start_queued_job(client, app):
    login(client)
    _enable_debrid(app)
    media = _series(client)
    link = _link(client, media["id"], "https://rapidgator.net/file/alpha", 1)
    debrid = FakeDebrid()
    downloader = FakeDownloader()
    now = datetime(2026, 10, 17, 5, tzinfo=timezone.utc)
    db = app.state.session_factory()
    tick(db, app.state.settings, now=now, debrid=debrid, downloader=downloader)
    row = db.get(Download, link["download_id"])
    assert row is not None and row.handle
    downloader.status = _offline  # type: ignore[method-assign]
    tick(db, app.state.settings, now=now, debrid=debrid, downloader=downloader)
    return db, row, downloader, debrid, now


def test_unreachable_journal_waits_for_threshold(client, app):
    _set_threshold(app, 15)
    db, row, downloader, debrid, now = _start_queued_job(client, app)
    try:
        tick(db, app.state.settings, now=now + timedelta(minutes=14), debrid=debrid, downloader=downloader)
        count = db.scalar(select(func.count()).select_from(Event).where(Event.type == "aria2.unreachable")) or 0
        assert count == 0

        tick(db, app.state.settings, now=now + timedelta(minutes=15), debrid=debrid, downloader=downloader)
        count = db.scalar(select(func.count()).select_from(Event).where(Event.type == "aria2.unreachable")) or 0
        assert count == 1
        db.refresh(row)
        assert row.state == "queued"
        assert row.handle
    finally:
        db.close()


def test_unreachable_journal_fires_once_per_outage(client, app):
    _set_threshold(app, 5)
    db, _row, downloader, debrid, now = _start_queued_job(client, app)
    try:
        for extra in (5, 7, 20):
            tick(
                db,
                app.state.settings,
                now=now + timedelta(minutes=extra),
                debrid=debrid,
                downloader=downloader,
            )
        count = db.scalar(select(func.count()).select_from(Event).where(Event.type == "aria2.unreachable")) or 0
        assert count == 1
    finally:
        db.close()


def test_unreachable_recovery_event_once(client, app):
    _set_threshold(app, 5)
    db, row, downloader, debrid, now = _start_queued_job(client, app)
    try:
        tick(db, app.state.settings, now=now + timedelta(minutes=6), debrid=debrid, downloader=downloader)
        assert (
            db.scalar(select(func.count()).select_from(Event).where(Event.type == "aria2.unreachable")) or 0
        ) == 1

        handle = row.handle
        assert handle

        def online(gid: str):
            return downloader._status[gid]

        downloader.status = online  # type: ignore[method-assign]
        tick(db, app.state.settings, now=now + timedelta(minutes=7), debrid=debrid, downloader=downloader)
        assert (
            db.scalar(select(func.count()).select_from(Event).where(Event.type == "aria2.reachable")) or 0
        ) == 1

        downloader.status = _offline  # type: ignore[method-assign]
        tick(db, app.state.settings, now=now + timedelta(minutes=20), debrid=debrid, downloader=downloader)
        tick(db, app.state.settings, now=now + timedelta(minutes=26), debrid=debrid, downloader=downloader)
        assert (
            db.scalar(select(func.count()).select_from(Event).where(Event.type == "aria2.unreachable")) or 0
        ) == 2
        assert (
            db.scalar(select(func.count()).select_from(Event).where(Event.type == "aria2.reachable")) or 0
        ) == 1
    finally:
        db.close()


def test_health_components_show_unreachable_duration(client, app, monkeypatch):
    _set_threshold(app, 15)
    db, _row, downloader, debrid, now = _start_queued_job(client, app)
    try:
        tick(db, app.state.settings, now=now + timedelta(minutes=20), debrid=debrid, downloader=downloader)
        since = datetime.now(timezone.utc) - timedelta(minutes=20)
        row = db.get(Setting, "system.aria2_unreachable_since")
        assert row is not None
        row.value = since.isoformat()
        db.commit()
    finally:
        db.close()

    login(client)

    from mediatracker.providers.base import Health
    from mediatracker.providers.downloaders import aria2 as aria2_mod

    def broken_health(_self):
        return Health(
            status="down",
            code="UNREACHABLE",
            message="aria2 est injoignable.",
            checked_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(aria2_mod.Aria2Downloader, "health", broken_health)

    body = client.get("/api/v1/health/components")
    assert body.status_code == 200, body.text
    aria2 = next(row for row in body.json()["components"] if row["name"] == "aria2")
    assert "depuis 20 min" in aria2["message"]
