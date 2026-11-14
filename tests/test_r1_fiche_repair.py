"""R1: one-click fiche repair endpoint."""

from datetime import datetime, timezone
from unittest.mock import patch

from sqlalchemy import select
from tests.conftest import csrf, login

from mediatracker.core.fiche_repair import FicheRepairReport, RepairStepResult
from mediatracker.core.forget import TRACE_FILE_ABSENT, forget_local_file
from mediatracker.db.models import Event, Media, MediaItem

NOW = datetime(2026, 10, 22, 18, tzinfo=timezone.utc)


def _series(db, title: str = "Brume Azur") -> Media:
    media = Media(type="series", title=title, year=2026, tmdb_id=88001, followed=True)
    db.add(media)
    db.flush()
    return media


def _episode(db, media: Media, season: int, episode: int, **kwargs) -> MediaItem:
    item = MediaItem(
        media_id=media.id,
        season=season,
        episode=episode,
        title=f"Épisode {episode}",
        air_date=datetime(2026, 1, episode).date(),
        state="missing",
        downloaded=False,
        **kwargs,
    )
    db.add(item)
    db.flush()
    return item


def _layout(app):
    data = app.state.settings.data_dir
    movies = data / "media" / "Films"
    series = data / "media" / "Series"
    movies.mkdir(parents=True, exist_ok=True)
    series.mkdir(parents=True, exist_ok=True)
    return movies, series


def _fake_report(**kwargs) -> FicheRepairReport:
    steps = kwargs.pop("steps", [RepairStepResult(name="disk", ok=True)])
    return FicheRepairReport(
        at=NOW,
        summary="Résumé fictif.",
        detail="0 fichier(s) détaché(s), 0 épisode(s) retiré(s). Aucun fichier n'a été supprimé du disque.",
        steps=steps,
        files_found=1,
        files_missing=0,
        links_available=2,
        corrected=["test"],
        file_status="present",
        **kwargs,
    )


def test_repair_runs_all_steps_and_persists_summary(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        media = _series(db)
        _episode(db, media, 1, 1)
        db.commit()
        media_id = media.id
    finally:
        db.close()

    calls: list[str] = []

    def track(name):
        def runner(*_args, **_kwargs):
            calls.append(name)
            if name == "status":
                return _fake_report()
            if name == "reanalyze":
                from mediatracker.library.scanner import TargetedScanReport

                return TargetedScanReport(found=1, new=0, absent=0)
            if name == "source":
                from mediatracker.core.source_poll import SourceRereadReport

                return SourceRereadReport(ok=True, error=None, links=2, message="2 liens.")
            return (1, "Jellyfin ok.", None)

        return runner

    fake_source = object()
    with (
        patch("mediatracker.providers.sources.loading.enabled_remote_sources", return_value=[(1, fake_source)]),
        patch("mediatracker.core.fiche_repair.refresh_library_claims", return_value=False),
        patch("mediatracker.core.fiche_repair.reanalyze_run", side_effect=track("reanalyze")),
        patch("mediatracker.core.fiche_repair.reread_source_for_repair", side_effect=track("source")),
        patch("mediatracker.core.fiche_repair._jellyfin_step", side_effect=track("jellyfin")),
    ):
        response = client.post(f"/api/v1/media/{media_id}/repair", headers=csrf(client))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["links_available"] == 2
    assert len(body["steps"]) == 6
    assert {step["name"] for step in body["steps"]} == {
        "cross_series",
        "disk",
        "reanalyze",
        "source",
        "jellyfin",
        "status",
    }
    assert "détaché" in body["detail"]
    assert calls == ["reanalyze", "source", "jellyfin"]

    db = app.state.session_factory()
    try:
        row = db.get(Media, media_id)
        assert row.last_repair_at is not None
        assert row.last_repair_summary
        event = db.scalar(select(Event).where(Event.type == "media.repaired", Event.media_id == media_id))
        assert event is not None
    finally:
        db.close()


def test_repair_continues_when_one_step_fails(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        media = _series(db, title="Cristal Nord")
        _episode(db, media, 1, 2)
        db.commit()
        media_id = media.id
    finally:
        db.close()

    def boom(*_args, **_kwargs):
        raise RuntimeError("panne fictive")

    fake_source = object()
    with (
        patch("mediatracker.providers.sources.loading.enabled_remote_sources", return_value=[(1, fake_source)]),
        patch("mediatracker.core.fiche_repair.refresh_library_claims", side_effect=boom),
        patch("mediatracker.core.fiche_repair.reanalyze_run") as reanalyze,
        patch("mediatracker.core.fiche_repair.reread_source_for_repair") as source,
        patch("mediatracker.core.fiche_repair._jellyfin_step", return_value=(0, "ok", None)),
    ):
        from mediatracker.library.scanner import TargetedScanReport

        reanalyze.return_value = TargetedScanReport(found=0, new=0, absent=0)
        from mediatracker.core.source_poll import SourceRereadReport

        source.return_value = SourceRereadReport(ok=True, error=None, links=0, message="")
        response = client.post(f"/api/v1/media/{media_id}/repair", headers=csrf(client))

    assert response.status_code == 200, response.text
    steps = {row["name"]: row for row in response.json()["steps"]}
    assert steps["disk"]["ok"] is False
    assert steps["reanalyze"]["ok"] is True
    assert steps["jellyfin"]["ok"] is True
    reanalyze.assert_called_once()
    source.assert_called_once()


def test_repair_never_sends_absent_files_to_trash(client, app, monkeypatch):
    login(client)
    movies, series = _layout(app)
    db = app.state.session_factory()
    try:
        media = _series(db, title="Fable Mer")
        item = _episode(db, media, 1, 3)
        item.downloaded = True
        item.file_path = str(series / "Fable.Mer.S01E03.mkv")
        forget_local_file(db, media, item, NOW)
        assert any(t.get("kind") == TRACE_FILE_ABSENT for t in (item.traces or []))
        db.commit()
        media_id = media.id
    finally:
        db.close()

    trash_calls: list[str] = []

    def forbid_trash(*_args, **_kwargs):
        trash_calls.append("trash")
        raise AssertionError("corbeille interdite")

    monkeypatch.setattr("mediatracker.core.cleanup._trash_file", forbid_trash)
    monkeypatch.setattr("mediatracker.library.regroup._send_to_trash", forbid_trash)

    fake_source = object()
    with (
        patch("mediatracker.providers.sources.loading.enabled_remote_sources", return_value=[(1, fake_source)]),
        patch("mediatracker.core.fiche_repair.reread_source_for_repair") as source,
        patch("mediatracker.core.fiche_repair._jellyfin_step", return_value=(0, "Jellyfin non configuré.", None)),
    ):
        from mediatracker.core.source_poll import SourceRereadReport

        source.return_value = SourceRereadReport(ok=True, error=None, links=0, message="")
        response = client.post(f"/api/v1/media/{media_id}/repair", headers=csrf(client))

    assert response.status_code == 200, response.text
    assert trash_calls == []


def test_repair_cross_series_detaches_foreign_file(client, app):
    login(client)
    _movies, series = _layout(app)
    db = app.state.session_factory()
    try:
        media = _series(db, title="Brume Azur")
        item = _episode(db, media, 1, 1)
        foreign = series / "Autre.Roche.2026" / "Autre.Roche.S01E01.mkv"
        foreign.parent.mkdir(parents=True, exist_ok=True)
        foreign.write_bytes(b"clip")
        item.file_path = str(foreign)
        item.downloaded = True
        db.commit()
        media_id = media.id
        item_id = item.id
    finally:
        db.close()

    fake_source = object()
    with (
        patch("mediatracker.library.repair._fetch", return_value=None),
        patch("mediatracker.providers.sources.loading.enabled_remote_sources", return_value=[(1, fake_source)]),
        patch("mediatracker.core.fiche_repair.reread_source_for_repair") as source,
        patch("mediatracker.core.fiche_repair._jellyfin_step", return_value=(0, "Jellyfin non configuré.", None)),
    ):
        from mediatracker.core.source_poll import SourceRereadReport

        source.return_value = SourceRereadReport(ok=True, error=None, links=0, message="")
        response = client.post(f"/api/v1/media/{media_id}/repair", headers=csrf(client))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["detached"] >= 1
    assert "épisode(s) retiré(s)" in body["detail"]
    assert "Aucun fichier n'a été supprimé du disque" in body["detail"]
    cross = next(step for step in body["steps"] if step["name"] == "cross_series")
    assert cross["ok"] is True

    db = app.state.session_factory()
    try:
        row = db.get(MediaItem, item_id)
        assert row is None or not row.file_path
        assert foreign.exists()
    finally:
        db.close()


def test_repair_returns_409_when_already_running(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        media = _series(db, title="Quartz Veil")
        _episode(db, media, 1, 1)
        db.commit()
        media_id = media.id
    finally:
        db.close()

    from mediatracker.core.fiche_repair import _lock_for

    lock = _lock_for(media_id)
    assert lock.acquire(blocking=False)
    try:
        response = client.post(f"/api/v1/media/{media_id}/repair", headers=csrf(client))
        assert response.status_code == 409
    finally:
        lock.release()
