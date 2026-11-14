"""R2: verify-all library job (batched fiche repair)."""

from datetime import datetime, timezone
from unittest.mock import patch

from sqlalchemy import select
from tests.conftest import csrf, login

from mediatracker.core.fiche_repair import FicheRepairReport, RepairStepResult
from mediatracker.core.library_guard import LIBRARY_SUSPENDED_KEY
from mediatracker.core.verify_all import BATCH_SIZE, reset_state
from mediatracker.db.models import Event, Media, MediaItem, Setting

NOW = datetime(2026, 10, 22, 20, tzinfo=timezone.utc)


def _media(db, title: str, *, followed: bool = True) -> Media:
    media = Media(type="series", title=title, year=2026, tmdb_id=90000 + hash(title) % 1000, followed=followed)
    db.add(media)
    db.flush()
    db.add(
        MediaItem(
            media_id=media.id,
            season=1,
            episode=1,
            title="Pilotage",
            air_date=datetime(2026, 1, 1).date(),
            state="missing",
        )
    )
    db.flush()
    return media


def _ok_report(title: str = "ok", **kwargs) -> FicheRepairReport:
    return FicheRepairReport(
        at=NOW,
        summary=f"Résumé {title}.",
        detail="Détail fictif.",
        steps=[RepairStepResult(name="disk", ok=True)],
        files_found=1,
        files_missing=kwargs.pop("files_missing", 0),
        links_available=1,
        file_status="present",
        **kwargs,
    )


def test_verify_all_processes_in_batches(client, app):
    reset_state()
    login(client)
    db = app.state.session_factory()
    try:
        count = BATCH_SIZE + 3
        for index in range(count):
            _media(db, f"Fiche Lot {index}")
        db.commit()
    finally:
        db.close()

    calls: list[int] = []

    def fake_pipeline(db, settings, media, **kwargs):
        calls.append(media.id)
        return _ok_report(media.title)

    with patch("mediatracker.core.verify_all.run_repair_pipeline", side_effect=fake_pipeline):
        start = client.post("/api/v1/library/verify-all", headers=csrf(client))
        assert start.status_code == 200, start.text
        assert start.json()["progress"]["running"] is True

        import time

        deadline = time.time() + 5
        while time.time() < deadline:
            state = client.get("/api/v1/library/verify-all")
            if not state.json()["progress"]["running"]:
                break
            time.sleep(0.05)

    assert len(calls) == count
    progress_events = db_events(app, "library.verify_all.progress")
    assert len(progress_events) == 2


def db_events(app, event_type: str) -> list[Event]:
    db = app.state.session_factory()
    try:
        return list(db.scalars(select(Event).where(Event.type == event_type)).all())
    finally:
        db.close()


def test_verify_all_returns_409_when_running(client, app):
    reset_state()
    login(client)
    from mediatracker.core.verify_all import _lock

    with _lock:
        import mediatracker.core.verify_all as mod

        mod._running = True
        mod._progress["running"] = True
        mod._progress["processed"] = 2
        mod._progress["total"] = 10
    try:
        response = client.post("/api/v1/library/verify-all", headers=csrf(client))
        assert response.status_code == 409
        body = response.json()
        assert body["progress"]["processed"] == 2
        assert body["progress"]["total"] == 10
    finally:
        with _lock:
            mod._running = False
            mod._progress["running"] = False
        reset_state()


def test_verify_all_summary_lists_problem_fiches(client, app):
    reset_state()
    login(client)
    db = app.state.session_factory()
    try:
        _media(db, "Cristal Nord")
        bad = _media(db, "Brume Azur")
        db.commit()
        bad_id = bad.id
    finally:
        db.close()

    def fake_pipeline(db, settings, media, **kwargs):
        if media.id == bad_id:
            return _ok_report(media.title, files_missing=2)
        return _ok_report(media.title)

    with patch("mediatracker.core.verify_all.run_repair_pipeline", side_effect=fake_pipeline):
        client.post("/api/v1/library/verify-all", headers=csrf(client))
        _wait_done(client)

    state = client.get("/api/v1/library/verify-all")
    summary = state.json()["last_summary"]
    assert summary is not None
    assert summary["processed"] == 2
    assert len(summary["problems"]) == 1
    assert summary["problems"][0]["media_id"] == bad_id
    assert summary["problems"][0]["title"] == "Brume Azur"

    db = app.state.session_factory()
    try:
        done = db.scalar(select(Event).where(Event.type == "library.verify_all.completed"))
        assert done is not None
        repaired = db.scalars(select(Event).where(Event.type == "media.repaired")).all()
        assert repaired == []
    finally:
        db.close()


def test_verify_all_skips_source_search_when_binding_healthy(client, app):
    reset_state()
    login(client)
    db = app.state.session_factory()
    try:
        media = _media(db, "Quartz Veil")
        db.commit()
        media_id = media.id
    finally:
        db.close()

    search_calls: list[str] = []

    def track_reread(db, media, provider_id, source, settings, **kwargs):
        from mediatracker.core.source_poll import SourceRereadReport

        search_calls.append("reread")
        return SourceRereadReport(ok=True, error=None, links=1, message="Page relue.")

    with (
        patch("mediatracker.core.source_binding.bindings_need_search", return_value=False),
        patch("mediatracker.core.fiche_repair.reread_source_for_repair", side_effect=track_reread),
        patch("mediatracker.core.fiche_repair._jellyfin_step", return_value=(0, "off", None)),
        patch("mediatracker.providers.sources.loading.enabled_remote_sources", return_value=[(1, object())]),
    ):
        client.post("/api/v1/library/verify-all", headers=csrf(client))
        _wait_done(client)

    assert search_calls == ["reread"]
    db = app.state.session_factory()
    try:
        row = db.get(Media, media_id)
        assert row is not None
        assert row.last_repair_summary
    finally:
        db.close()


def test_verify_all_stops_when_library_suspended(client, app):
    reset_state()
    login(client)
    db = app.state.session_factory()
    try:
        for index in range(5):
            _media(db, f"Fable Mer {index}")
        db.add(Setting(key=LIBRARY_SUSPENDED_KEY, value="1", is_secret=False, updated_at=NOW))
        db.commit()
    finally:
        db.close()

    with patch("mediatracker.core.verify_all.run_repair_pipeline", side_effect=lambda *a, **k: _ok_report("x")):
        client.post("/api/v1/library/verify-all", headers=csrf(client))
        _wait_done(client)

    state = client.get("/api/v1/library/verify-all")
    summary = state.json()["last_summary"]
    assert summary["stopped_early"] is True
    assert summary["stop_reason"] == "library_suspended"
    assert summary["processed"] == 0


def _wait_done(client) -> None:
    import time

    deadline = time.time() + 5
    while time.time() < deadline:
        state = client.get("/api/v1/library/verify-all")
        if not state.json()["progress"]["running"]:
            return
        time.sleep(0.05)
    raise AssertionError("verify-all did not finish")
