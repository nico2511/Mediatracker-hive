"""Collecte task_run : tampon, interrupteur, API, graphe."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from tests.conftest import csrf, login

from mediatracker.db.models import TaskRun
from mediatracker.metro.cause import scrub_cause, scrub_exception_cause
from mediatracker.metro.collection import is_collection_active, set_collection_duration
from mediatracker.metro.graph import EDGE_BY_ID, INSTRUMENTED_PR1, NOT_INSTRUMENTED_PR1, STATIONS
from mediatracker.metro.record import (
    clear_blocked,
    flush_pass_buffer,
    load_pass_collection_flag,
    metro_pass,
    record_blocked_continuous,
    record_task_run,
    reset_metro_instrumentation_state,
)


@pytest.fixture(autouse=True)
def _reset_metro_state():
    reset_metro_instrumentation_state()
    yield
    reset_metro_instrumentation_state()


def test_scrub_cause_strips_url_and_path():
    assert scrub_cause("https://mediatracker.example.com/x?token=secret") == "[lien]"
    assert "[chemin]" in (scrub_cause("/data/library/series/show/file.mp4") or "")


def test_scrub_exception_cause_no_message_body():
    assert scrub_exception_cause(RuntimeError("secret path /movies/title.mkv")) == "RuntimeError"


def test_graph_edges_and_instrumented_partition():
    assert len(EDGE_BY_ID) == 72
    assert INSTRUMENTED_PR1.isdisjoint(NOT_INSTRUMENTED_PR1)
    assert len(INSTRUMENTED_PR1) + len(NOT_INSTRUMENTED_PR1) == len(STATIONS)


def test_collection_toggle(client, app):
    login(client)
    with app.state.session_factory() as db:
        assert not is_collection_active(db)
        set_collection_duration(db, "1h")
        db.commit()
        assert is_collection_active(db)
        set_collection_duration(db, "off")
        db.commit()
        assert not is_collection_active(db)


def test_pass_buffer_no_db_write_until_flush(client, app):
    login(client)
    factory = app.state.session_factory
    with factory() as db:
        set_collection_duration(db, "6h")
        db.commit()
    load_pass_collection_flag(factory)
    record_task_run(station="DL1", trigger="schedule", outcome="ok")
    with factory() as db:
        assert db.scalars(select(TaskRun)).all() == []
    assert flush_pass_buffer(factory) == 1
    with factory() as db:
        assert len(db.scalars(select(TaskRun)).all()) == 1


def test_blocked_continuous_single_line_and_close(client, app, monkeypatch):
    login(client)
    factory = app.state.session_factory
    with factory() as db:
        set_collection_duration(db, "24h")
        db.commit()
    mono = {"v": 0.0}
    monkeypatch.setattr("mediatracker.metro.record._monotonic", lambda: mono["v"])
    load_pass_collection_flag(factory)
    record_blocked_continuous(station="DL1", blocked_by="DL9:held")
    record_blocked_continuous(station="DL1", blocked_by="DL9:held")
    assert flush_pass_buffer(factory) == 1
    from mediatracker.metro import record as metro_record

    blocked = metro_record._open_blocked.get(("DL1", "DL9:held"))
    assert blocked is not None and blocked.db_id is not None
    with factory() as db:
        rows = db.scalars(select(TaskRun).where(TaskRun.blocked_by == "DL9:held")).all()
        assert len(rows) == 1
        row_id = rows[0].id
        first_hb = rows[0].heartbeat_at

    later = first_hb + timedelta(minutes=2)
    load_pass_collection_flag(factory)
    assert blocked.db_id == row_id
    mono["v"] = 70.0
    monkeypatch.setattr("mediatracker.metro.record._utcnow", lambda: later)
    from mediatracker.metro.record import pending_buffer_size

    record_blocked_continuous(station="DL1", blocked_by="DL9:held")
    assert pending_buffer_size() == 1
    assert flush_pass_buffer(factory) >= 0
    with factory() as db:
        row = db.get(TaskRun, row_id)
        assert row is not None
        assert row.heartbeat_at == later
    load_pass_collection_flag(factory)
    end = later + timedelta(minutes=1)
    monkeypatch.setattr("mediatracker.metro.record._utcnow", lambda: end)
    clear_blocked("DL1", "DL9:held")
    flush_pass_buffer(factory)
    with factory() as db:
        row = db.get(TaskRun, row_id)
        assert row is not None
        assert row.finished_at == end


def test_no_write_when_collection_off(client, app, monkeypatch):
    login(client)
    factory = app.state.session_factory
    with factory() as db:
        set_collection_duration(db, "off")
        db.commit()
    load_pass_collection_flag(factory)
    record_blocked_continuous(station="DL1", blocked_by="DL9:held")
    record_task_run(station="RNG5", trigger="event", outcome="ok")
    assert flush_pass_buffer(factory) == 0


def test_purge_parent_child_no_fk_error(client, app):
    login(client)
    factory = app.state.session_factory
    with factory() as db:
        set_collection_duration(db, "1h")
        db.commit()
        old = datetime.now(timezone.utc) - timedelta(days=10)
        parent = TaskRun(
            line="Socle",
            station="SOC1",
            trigger="schedule",
            outcome="ok",
            started_at=old,
            finished_at=old,
            heartbeat_at=old,
        )
        child = TaskRun(
            line="Telechargements",
            station="DL1",
            trigger="schedule",
            outcome="ok",
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            heartbeat_at=datetime.now(timezone.utc),
        )
        db.add(parent)
        db.flush()
        child.parent_run_id = parent.id
        db.add(child)
        db.commit()
        parent_id = parent.id
    from sqlalchemy import delete

    from mediatracker.metro.record import RETENTION_DAYS

    with factory() as db:
        cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
        db.execute(delete(TaskRun).where(TaskRun.started_at < cutoff))
        db.commit()
        row = db.get(TaskRun, parent_id)
        assert row is None
        survivors = db.scalars(select(TaskRun)).all()
        assert len(survivors) == 1
        assert survivors[0].parent_run_id is None


def test_metro_pass_survives_flush_failure(client, app, monkeypatch):
    from sqlalchemy.exc import OperationalError

    login(client)
    factory = app.state.session_factory
    with factory() as db:
        set_collection_duration(db, "1h")
        db.commit()

    def boom(self):
        raise OperationalError("disk I/O error", None, None)

    monkeypatch.setattr("sqlalchemy.orm.Session.commit", boom)
    load_pass_collection_flag(factory)
    with metro_pass():
        record_task_run(station="DL1", trigger="schedule", outcome="ok")
    assert flush_pass_buffer(factory) == 0


def test_task_run_api(client, app):
    login(client)
    factory = app.state.session_factory
    with factory() as db:
        set_collection_duration(db, "1h")
        db.commit()
    load_pass_collection_flag(factory)
    record_task_run(station="RNG5", trigger="event", outcome="ok", subject_ref="download:99")
    flush_pass_buffer(factory)
    resp = client.get("/api/v1/task-runs?hours=24&station=RNG5", headers=csrf(client))
    assert resp.status_code == 200
    assert resp.json()["count"] >= 1


def test_task_run_collection_admin_only(client, app):
    login(client)
    write_token = client.post(
        "/api/v1/tokens",
        headers=csrf(client),
        json={"name": "test-write", "scopes": ["read", "write"]},
    ).json()["token"]
    denied = client.patch(
        "/api/v1/task-runs/collection",
        headers={"Authorization": f"Bearer {write_token}"},
        json={"duration": "24h"},
    )
    assert denied.status_code == 403
    ok = client.patch(
        "/api/v1/task-runs/collection",
        headers=csrf(client),
        json={"duration": "24h"},
    )
    assert ok.status_code == 200


def test_simulated_download_sequence(client, app):
    login(client)
    from mediatracker.metro.instrument import (
        instrument_download_started,
        instrument_finalize,
        instrument_import_ready,
        instrument_magnet_cache,
        instrument_source_apply_link,
        instrument_unlock,
    )

    factory = app.state.session_factory
    with factory() as db:
        set_collection_duration(db, "24h")
        db.commit()
    load_pass_collection_flag(factory)
    instrument_source_apply_link(42)
    instrument_download_started(42)
    instrument_magnet_cache(42)
    instrument_unlock(42)
    instrument_import_ready(42)
    instrument_finalize(42)
    flush_pass_buffer(factory)
    resp = client.get("/api/v1/task-runs?hours=24", headers=csrf(client))
    stations = {row["station"] for row in resp.json()["items"]}
    assert {"SRC8", "DL1", "ADB2", "RNG1", "RNG5"}.issubset(stations)


def test_metro_pass_reraises_business_error(client, app):
    login(client)
    factory = app.state.session_factory
    with factory() as db:
        set_collection_duration(db, "1h")
        db.commit()
    load_pass_collection_flag(factory)
    with pytest.raises(RuntimeError, match="passe métier"):
        with metro_pass():
            raise RuntimeError("passe métier")


def test_ten_empty_passes_no_soc1(client, app):
    login(client)
    factory = app.state.session_factory
    with factory() as db:
        set_collection_duration(db, "24h")
        db.commit()
    for _ in range(10):
        load_pass_collection_flag(factory)
        with metro_pass():
            pass
        flush_pass_buffer(factory)
    with factory() as db:
        soc_rows = db.scalars(select(TaskRun).where(TaskRun.station == "SOC1")).all()
        assert len(soc_rows) == 0


def test_jf4_one_line_per_scan_phase(client, app):
    from mediatracker.metro.instrument import instrument_jellyfin_wait_scan

    login(client)
    factory = app.state.session_factory
    with factory() as db:
        set_collection_duration(db, "24h")
        db.commit()
    load_pass_collection_flag(factory)
    for _ in range(5):
        instrument_jellyfin_wait_scan(scanning=True)
    instrument_jellyfin_wait_scan(scanning=False)
    flush_pass_buffer(factory)
    with factory() as db:
        rows = db.scalars(select(TaskRun).where(TaskRun.station == "JF4")).all()
        assert len(rows) == 2
        causes = {row.cause for row in rows}
        assert "Scan Jellyfin terminé" in causes


def test_collection_duration_exposed(client, app):
    login(client)
    client.patch(
        "/api/v1/task-runs/collection",
        headers=csrf(client),
        json={"duration": "6h"},
    )
    body = client.get("/api/v1/task-runs/collection", headers=csrf(client)).json()
    assert body["active"] is True
    assert body["duration"] == "6h"


def test_block_no_write_after_collection_off(client, app, monkeypatch):
    login(client)
    factory = app.state.session_factory
    with factory() as db:
        set_collection_duration(db, "1h")
        db.commit()
    load_pass_collection_flag(factory)
    record_blocked_continuous(station="DL1", blocked_by="ADB1:AUTH_BLOCKED")
    flush_pass_buffer(factory)
    with factory() as db:
        assert len(db.scalars(select(TaskRun)).all()) == 1
    with factory() as db:
        set_collection_duration(db, "off")
        db.commit()
    load_pass_collection_flag(factory)
    times = iter([0.0, 70.0])
    monkeypatch.setattr("mediatracker.metro.record._monotonic", lambda: next(times))
    record_blocked_continuous(station="DL1", blocked_by="ADB1:AUTH_BLOCKED")
    assert flush_pass_buffer(factory) == 0
    with factory() as db:
        assert len(db.scalars(select(TaskRun)).all()) == 1


def test_soc7_block_survives_contention_then_closes(client, app, monkeypatch):
    from mediatracker.metro.instrument import (
        instrument_sqlite_locked,
        instrument_worker_pass_soc_unblock,
    )

    login(client)
    factory = app.state.session_factory
    with factory() as db:
        set_collection_duration(db, "24h")
        db.commit()
    mono = {"v": 0.0}
    monkeypatch.setattr("mediatracker.metro.record._monotonic", lambda: mono["v"])
    row_id = None
    for i in range(5):
        load_pass_collection_flag(factory)
        mono["v"] = float(i * 70)
        with metro_pass():
            instrument_sqlite_locked(label="base occupée")
        instrument_worker_pass_soc_unblock()
        flush_pass_buffer(factory)
    from mediatracker.metro import record as metro_record

    with factory() as db:
        rows = db.scalars(select(TaskRun).where(TaskRun.blocked_by == "SOC7:locked")).all()
        assert len(rows) == 1
        row_id = rows[0].id
    assert ("SOC1", "SOC7:locked") in metro_record._open_blocked
    load_pass_collection_flag(factory)
    with metro_pass():
        pass
    instrument_worker_pass_soc_unblock()
    flush_pass_buffer(factory)
    with factory() as db:
        row = db.get(TaskRun, row_id)
        assert row is not None
        assert row.finished_at is not None


def test_source_poll_work_writes_two_lines(client, app):
    from mediatracker.metro.instrument import instrument_source_poll

    login(client)
    factory = app.state.session_factory
    with factory() as db:
        set_collection_duration(db, "1h")
        db.commit()
    load_pass_collection_flag(factory)
    instrument_source_poll(did_work=True)
    instrument_source_poll(did_work=True)
    flush_pass_buffer(factory)
    with factory() as db:
        rows = db.scalars(select(TaskRun).where(TaskRun.station == "SRC1", TaskRun.outcome == "ok")).all()
        assert len(rows) == 2
