"""Journal API filters, scrubbing, pagination, performance, and retention."""

import os
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session
from tests.conftest import csrf, login

from mediatracker.db.models import Event
from mediatracker.events import record_event
from mediatracker.events.journal import list_journal_events
from mediatracker.events.retention import maybe_purge_old_events


def _seed_event(
    db: Session,
    *,
    type_: str,
    when: datetime,
    media_id: int | None = None,
    level: str = "info",
    payload: dict | None = None,
) -> Event:
    return record_event(
        db,
        type_,
        level=level,
        media_id=media_id,
        payload=payload or {"title": "Série Exemple"},
        when=when,
    )


def test_journal_scrubs_secrets_from_payload(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        moment = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
        record_event(
            db,
            "download.failed",
            payload={
                "title": "Film Démo",
                "error": "timeout",
                "token": "secret-journal-token",
                "direct_url": "https://alldebrid.com/d/abc",
            },
            when=moment,
        )
        db.commit()
    finally:
        db.close()
    listed = client.get("/api/v1/events", params={"type": "download.failed"})
    assert listed.status_code == 200
    item = listed.json()["items"][0]
    assert item["label"] == "Téléchargement échoué"
    assert "token" not in item["payload"]
    assert "direct_url" not in item["payload"]
    assert item["payload"]["error"] == "timeout"


def test_journal_filters_category_media_level_and_dates(client, app):
    login(client)
    created = client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Fiche Alpha", "year": 2020},
        headers=csrf(client),
    )
    assert created.status_code == 201
    media_id = created.json()["id"]
    db = app.state.session_factory()
    base = datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)
    try:
        _seed_event(db, type_="download.failed", when=base, media_id=media_id, level="warning")
        _seed_event(db, type_="download.completed", when=base + timedelta(hours=1), media_id=media_id)
        _seed_event(db, type_="library.imported", when=base + timedelta(hours=2))
        db.commit()
    finally:
        db.close()

    by_cat = client.get("/api/v1/events", params={"category": "download."})
    assert {row["type"] for row in by_cat.json()["items"]} == {"download.failed", "download.completed"}

    by_media = client.get("/api/v1/events", params={"media_id": media_id, "category": "download."})
    assert len(by_media.json()["items"]) == 2

    by_level = client.get("/api/v1/events", params={"level": "warning"})
    assert len(by_level.json()["items"]) == 1
    assert by_level.json()["items"][0]["type"] == "download.failed"

    window = client.get(
        "/api/v1/events",
        params={
            "since": (base + timedelta(minutes=30)).isoformat(),
            "until": (base + timedelta(hours=90)).isoformat(),
        },
    )
    types = {row["type"] for row in window.json()["items"]}
    assert types == {"download.completed", "library.imported"}


def test_journal_filters_by_download_id_in_payload(client, app):
    login(client)
    db = app.state.session_factory()
    moment = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
    try:
        _seed_event(
            db,
            type_="download.started",
            when=moment,
            payload={"download_id": 42, "title": "Brume Alpha"},
        )
        _seed_event(
            db,
            type_="download.completed",
            when=moment + timedelta(minutes=5),
            payload={"download_id": 43, "title": "Nappe Beta"},
        )
        db.commit()
    finally:
        db.close()

    filtered = client.get("/api/v1/events", params={"download_id": 42})
    assert filtered.status_code == 200
    items = filtered.json()["items"]
    assert len(items) == 1
    assert items[0]["type"] == "download.started"
    assert items[0]["payload"]["download_id"] == 42


def test_journal_search_and_length_limit(client, app):
    login(client)
    db = app.state.session_factory()
    moment = datetime(2026, 8, 1, tzinfo=timezone.utc)
    try:
        _seed_event(db, type_="download.failed", when=moment, payload={"title": "Alpha"})
        _seed_event(db, type_="library.imported", when=moment + timedelta(minutes=1))
        db.commit()
    finally:
        db.close()

    hits = client.get("/api/v1/events", params={"q": "échoué"})
    assert hits.status_code == 200
    assert all("failed" in row["type"] for row in hits.json()["items"])

    too_long = client.get("/api/v1/events", params={"q": "x" * 81})
    assert too_long.status_code == 422


def test_journal_cursor_uses_ts_and_id(client, app):
    login(client)
    db = app.state.session_factory()
    t0 = datetime(2026, 7, 1, 8, 0, tzinfo=timezone.utc)
    try:
        first = _seed_event(
            db,
            type_="source.checked",
            when=t0,
            payload={"result": "nothing", "message": "Premier contrôle factice."},
        )
        second = _seed_event(
            db,
            type_="source.checked",
            when=t0,
            payload={"result": "nothing", "message": "Second contrôle factice."},
        )
        db.commit()
        assert first.ts == second.ts
        assert second.id > first.id
    finally:
        db.close()

    page1 = client.get("/api/v1/events", params={"limit": 1, "type": "source.checked"})
    assert len(page1.json()["items"]) == 1
    assert page1.json()["items"][0]["id"] == second.id
    cursor = page1.json()["next_cursor"]
    assert cursor

    page2 = client.get("/api/v1/events", params={"limit": 1, "type": "source.checked", "cursor": cursor})
    assert page2.json()["items"][0]["id"] == first.id
    assert page2.json()["next_cursor"] is None


def test_event_retention_purges_old_rows_in_batches(client, app):
    db = app.state.session_factory()
    now = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    try:
        for day in range(120):
            moment = now - timedelta(days=day)
            _seed_event(
                db,
                type_="source.checked",
                when=moment,
                payload={"result": "nothing", "message": f"Contrôle factice jour {day}."},
            )
        db.commit()
        assert db.query(Event).count() == 120

        removed = maybe_purge_old_events(db, now=now, batch_size=10)
        db.commit()
        # Jours 0–30 conservés (strictement plus jeunes que now - 30 j).
        assert removed == 89
        assert db.query(Event).count() == 31

        again = maybe_purge_old_events(db, now=now + timedelta(hours=1))
        assert again == 0

        later = maybe_purge_old_events(db, now=now + timedelta(days=2))
        db.commit()
        assert later == 2
        assert db.query(Event).count() == 29
    finally:
        db.close()


def test_journal_list_performance_on_large_table(client, app):
    if os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("JOURNAL_PERF") != "1":
        pytest.skip("performance bench disabled in CI unless JOURNAL_PERF=1")

    db = app.state.session_factory()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    try:
        existing = db.query(Event).count()
        target = 100_000
        for offset in range(target - existing):
            record_event(
                db,
                "download.completed" if offset % 2 == 0 else "library.imported",
                when=start + timedelta(seconds=offset),
                media_id=None,
            )
            if offset and offset % 5000 == 0:
                db.commit()
        db.commit()
        assert db.query(Event).count() >= target
    finally:
        db.close()

    db = app.state.session_factory()
    try:
        t0 = time.perf_counter()
        rows, _cursor = list_journal_events(
            db,
            limit=50,
            cursor=None,
            event_type=None,
            category=None,
            media_id=None,
            download_id=None,
            level=None,
            since=None,
            until=None,
            search=None,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert len(rows) == 50
    finally:
        db.close()

    limit_ms = 2000.0 if os.environ.get("GITHUB_ACTIONS") == "true" else 100.0
    assert elapsed_ms < limit_ms, f"liste journal trop lente ({elapsed_ms:.1f} ms, seuil {limit_ms} ms)"
