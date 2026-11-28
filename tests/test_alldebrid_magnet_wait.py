"""AllDebrid magnet wait: bounded polling, fast worker ticks, ADB2 outcomes, wait logs."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from tests.conftest import PLAUSIBLE_VIDEO_BYTES, csrf, login
from tests.test_comet import _CacheDebrid, _series_demo, _stub_source
from tests.test_pipeline import FakeDebrid, FakeDownloader, _enable_debrid, _link

from mediatracker.core.magnet_claim import MagnetClaimPending, claim_cached_magnet
from mediatracker.core.magnet_wait import ALLDEBRID_WAIT_CODE, is_magnet_wait
from mediatracker.core.pipeline import tick
from mediatracker.core.pipeline.jobs import _aware
from mediatracker.db.models import Download, Link, Provider, TaskRun
from mediatracker.metro.collection import set_collection_duration
from mediatracker.metro.record import (
    flush_pass_buffer,
    load_pass_collection_flag,
    metro_pass,
    reset_metro_instrumentation_state,
)
from mediatracker.providers.base import DirectLink, LinkCandidate
from mediatracker.providers.debrid.alldebrid import AllDebridError, MagnetFile, MagnetSnapshot


class _NeverReadyDebrid(FakeDebrid):
    """Cached magnet that never becomes ready; hoster links stay unavailable."""

    def __init__(self) -> None:
        super().__init__()
        self.mode = "dead"
        self.uploaded: list[str] = []
        self.status_calls = 0
        self.next_magnet_id = 99

    def magnet_cache(self, magnets: list[str]):
        return [type("Hit", (), {"magnet": magnets[0], "cached": True})()]

    def upload_magnet(self, magnet: str) -> MagnetSnapshot:
        self.uploaded.append(magnet)
        mid = str(self.next_magnet_id)
        self.next_magnet_id += 1
        return MagnetSnapshot(mid, False, [])

    def magnet_status(self, magnet_id: str) -> MagnetSnapshot:
        self.status_calls += 1
        return MagnetSnapshot(magnet_id, False, [])

    def delete_magnet(self, magnet_id: str) -> None:
        del magnet_id

    def known_files(self):
        return []


class _CometNeverReadyDebrid(_CacheDebrid):
    def __init__(self) -> None:
        super().__init__(cached=True, ready=False)

    def magnet_status(self, magnet_id: str) -> MagnetSnapshot:
        self.status_calls = getattr(self, "status_calls", 0) + 1
        return MagnetSnapshot(magnet_id, False, [])


def _stub_comet_magnet_and_hoster(monkeypatch):
    magnet = LinkCandidate(
        url="magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        kind="magnet",
        quality="1080p",
        language="MULTI",
        size=PLAUSIBLE_VIDEO_BYTES,
        filename="Exemple.S01E01.1080p.WEB.H264.MULTi.mkv",
        season=1,
        episode=1,
        file_index=0,
        external_id="comet:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:0",
    )
    hoster = LinkCandidate(
        url="https://rapidgator.net/file/abc",
        kind="ddl_hoster",
        hoster="rapidgator",
        quality="1080p",
        language="MULTI",
        size=PLAUSIBLE_VIDEO_BYTES,
        filename="Exemple.S01E01.1080p.WEB.H264.MULTi.mkv",
        season=1,
        episode=1,
        external_id="comet:rapidgator:0",
    )

    class _Source:
        type = "comet"

        def links_for(self, query, preferences):
            del query, preferences
            return [magnet, hoster]

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "mediatracker.providers.sources.loading.episode_source",
        lambda name, settings, *, db=None, base_url=None, base_urls=None, transport=None: _Source(),
    )


def _stub_two_comet_magnets(monkeypatch):
    magnets = [
        LinkCandidate(
            url="magnet:?xt=urn:btih:11111111111111111111111111111111111111111111",
            kind="magnet",
            quality="1080p",
            language="MULTI",
            size=PLAUSIBLE_VIDEO_BYTES,
            filename="Exemple.S01E01.mkv",
            season=1,
            episode=1,
            file_index=0,
            external_id="comet:11111111111111111111111111111111111111111111:0",
        ),
        LinkCandidate(
            url="magnet:?xt=urn:btih:22222222222222222222222222222222222222222222",
            kind="magnet",
            quality="1080p",
            language="MULTI",
            size=PLAUSIBLE_VIDEO_BYTES,
            filename="Exemple.S01E01.mkv",
            season=1,
            episode=1,
            file_index=0,
            external_id="comet:22222222222222222222222222222222222222222222:0",
        ),
    ]

    class _Source:
        type = "comet"

        def links_for(self, query, preferences):
            del query, preferences
            return magnets

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "mediatracker.providers.sources.loading.episode_source",
        lambda name, settings, *, db=None, base_url=None, base_urls=None, transport=None: _Source(),
    )


class _HosterFallbackDebrid(FakeDebrid):
    def __init__(self) -> None:
        super().__init__()
        self.uploaded: list[str] = []
        self.status_calls = 0

    def magnet_cache(self, magnets: list[str]):
        return [type("Hit", (), {"magnet": magnets[0], "cached": True})()]

    def upload_magnet(self, magnet: str) -> MagnetSnapshot:
        self.uploaded.append(magnet)
        return MagnetSnapshot("99", False, [])

    def magnet_status(self, magnet_id: str) -> MagnetSnapshot:
        self.status_calls += 1
        return MagnetSnapshot(magnet_id, False, [])

    def delete_magnet(self, magnet_id: str) -> None:
        del magnet_id

    def known_files(self):
        return []


def test_nonblocking_claim_does_not_sleep():
    debrid = _NeverReadyDebrid()
    sleeps: list[float] = []

    def sleeper(seconds: float) -> None:
        sleeps.append(seconds)

    result = claim_cached_magnet(
        debrid,
        "magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        file_index=0,
        filename="Exemple.S01E01.mkv",
        blocking_wait=False,
        sleeper=sleeper,
    )
    assert isinstance(result, MagnetClaimPending)
    assert result.magnet_id == "99"
    assert sleeps == []
    assert len(debrid.uploaded) == 1


def test_blocking_claim_still_waits_for_unit_tests():
    debrid = _NeverReadyDebrid()
    sleeps: list[float] = []

    result = claim_cached_magnet(
        debrid,
        "magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        file_index=0,
        filename="Exemple.S01E01.mkv",
        wait_seconds=4,
        poll_interval=2,
        blocking_wait=True,
        sleeper=lambda s: sleeps.append(s),
    )
    assert result is None
    assert len(sleeps) >= 1


def test_pending_magnet_single_upload_many_ticks(client, app, monkeypatch):
    login(client)
    _enable_debrid(app)
    media = _series_demo(client, app, order="comet")
    link = _link(client, media["id"], "https://rapidgator.net/file/abc", 1)
    _stub_source(monkeypatch)
    debrid = _NeverReadyDebrid()
    downloader = FakeDownloader()
    now = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        for tick_index in range(4):
            tick(db, app.state.settings, now=now + timedelta(minutes=tick_index), debrid=debrid, downloader=downloader)
            row = db.get(Download, link["download_id"])
            assert is_magnet_wait(row)
            assert row.error_code == ALLDEBRID_WAIT_CODE
        assert len(debrid.uploaded) == 1
        assert debrid.status_calls == 4
    finally:
        db.close()


def test_magnet_wait_timeout_falls_back_to_hoster(client, app, monkeypatch):
    login(client)
    _enable_debrid(app)
    media = _series_demo(client, app, order="comet")
    link = _link(client, media["id"], "https://rapidgator.net/file/ignore", 1)
    _stub_comet_magnet_and_hoster(monkeypatch)
    debrid = _HosterFallbackDebrid()
    downloader = FakeDownloader()
    start = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        for row in db.scalars(select(Link)).all():
            db.delete(row)
        db.commit()
        tick(db, app.state.settings, now=start, debrid=debrid, downloader=downloader)
        row = db.get(Download, link["download_id"])
        assert is_magnet_wait(row)
        row.attempt_started_at = start - timedelta(seconds=700)
        db.commit()
        tick(db, app.state.settings, now=start + timedelta(minutes=12), debrid=debrid, downloader=downloader)
        row = db.get(Download, link["download_id"])
        assert not is_magnet_wait(row)
        assert len(debrid.uploaded) == 1
        assert downloader.urls == ["https://cdn.example/secret.mkv"]
        magnets = db.scalars(select(Link).where(Link.kind == "magnet")).all()
        assert magnets and not magnets[0].dead
    finally:
        db.close()


def test_tick_duration_without_blocking_sleep(client, app, monkeypatch):
    login(client)
    _enable_debrid(app)
    media = _series_demo(client, app, order="comet")
    link = _link(client, media["id"], "https://rapidgator.net/file/abc", 1)
    _stub_source(monkeypatch)
    debrid = _NeverReadyDebrid()
    db = app.state.session_factory()
    try:
        before = time.perf_counter()
        tick(
            db,
            app.state.settings,
            now=datetime(2026, 10, 12, tzinfo=timezone.utc),
            debrid=debrid,
            downloader=FakeDownloader(),
        )
        elapsed_ms = (time.perf_counter() - before) * 1000
        assert elapsed_ms < 3000, f"tick took {elapsed_ms:.0f} ms (blocking wait regression)"
        row = db.get(Download, link["download_id"])
        assert is_magnet_wait(row)
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _metro_reset():
    reset_metro_instrumentation_state()
    yield
    reset_metro_instrumentation_state()


def test_adb2_one_deferred_line_per_wait(client, app, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    login(client)
    _enable_debrid(app)
    factory = app.state.session_factory
    with factory() as db:
        set_collection_duration(db, "24h")
        db.commit()
    load_pass_collection_flag(factory)
    media = _series_demo(client, app, order="comet")
    link = _link(client, media["id"], "https://rapidgator.net/file/ignore", 1)
    _stub_source(monkeypatch)
    debrid = _NeverReadyDebrid()
    start = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        row = db.get(Download, link["download_id"])
        adb2 = select(TaskRun).where(
            TaskRun.station == "ADB2",
            TaskRun.subject_ref == f"download:{row.id}",
        )
        with metro_pass():
            for i in range(3):
                tick(
                    db,
                    app.state.settings,
                    now=start + timedelta(seconds=30 * i),
                    debrid=debrid,
                    downloader=FakeDownloader(),
                )
            flush_pass_buffer(factory)
            rows = db.scalars(adb2).all()
            assert len(rows) == 1
            assert rows[0].outcome == "deferred"
            row = db.get(Download, link["download_id"])
            row.attempt_started_at = start - timedelta(seconds=700)
            db.commit()
            tick(db, app.state.settings, now=start + timedelta(hours=1), debrid=debrid, downloader=FakeDownloader())
        flush_pass_buffer(factory)
        db.expire_all()
        rows = db.scalars(adb2).all()
        assert len(rows) == 1
        assert rows[0].outcome == "error"
        assert rows[0].finished_at is not None
        begin_lines = [r for r in caplog.records if "attente aimant démarrée" in r.message]
        assert len(begin_lines) == 1
        poll_begin = [r for r in caplog.records if "attente aimant démarrée" in r.message and "délai_max" in r.message]
        assert poll_begin
        end_lines = [r for r in caplog.records if "attente aimant expirée" in r.message]
        assert len(end_lines) == 1
        assert any(token in end_lines[0].message for token in ("repli", "hébergeur", "source"))
    finally:
        db.close()


def test_magnet_wait_poll_interval_on_row(client, app, monkeypatch):
    login(client)
    _enable_debrid(app)
    media = _series_demo(client, app, order="comet")
    link = _link(client, media["id"], "https://rapidgator.net/file/abc", 1)
    _stub_source(monkeypatch)
    debrid = _NeverReadyDebrid()
    now = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=now, debrid=debrid, downloader=FakeDownloader())
        row = db.get(Download, link["download_id"])
        assert is_magnet_wait(row)
        poll_s = app.state.settings.alldebrid_magnet_poll_seconds
        assert poll_s >= 5
        expected = now + timedelta(seconds=poll_s)
        assert _aware(row.next_retry_at) == expected
    finally:
        db.close()


def test_resume_magnet_wait_after_worker_restart(client, app, monkeypatch):
    login(client)
    _enable_debrid(app)
    media = _series_demo(client, app, order="comet")
    link = _link(client, media["id"], "https://rapidgator.net/file/abc", 1)
    _stub_source(monkeypatch)
    debrid = _NeverReadyDebrid()
    start = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=start, debrid=debrid, downloader=FakeDownloader())
        row = db.get(Download, link["download_id"])
        assert is_magnet_wait(row)
        db.expunge_all()
        db2 = app.state.session_factory()
        try:
            row2 = db2.get(Download, link["download_id"])
            assert is_magnet_wait(row2)
            def ready_status(magnet_id: str) -> MagnetSnapshot:
                files = [MagnetFile(0, "Exemple.S01E01.mkv", 40, "https://alldebrid.com/f/demo")]
                return MagnetSnapshot(magnet_id, True, files)

            debrid.magnet_status = ready_status  # type: ignore[method-assign]

            def unlock_alldebrid(url: str) -> DirectLink:
                if url.startswith("https://alldebrid.com/"):
                    return DirectLink(
                        url="https://cdn.example/comet.mkv",
                        filename="Exemple.S01E01.mkv",
                        size=40,
                    )
                return FakeDebrid.unlock(debrid, url)

            debrid.unlock = unlock_alldebrid  # type: ignore[method-assign]
            downloader = FakeDownloader()
            tick(
                db2,
                app.state.settings,
                now=start + timedelta(minutes=5),
                debrid=debrid,
                downloader=downloader,
            )
            row2 = db2.get(Download, link["download_id"])
            assert not is_magnet_wait(row2)
            assert downloader.urls == ["https://cdn.example/comet.mkv"]
        finally:
            db2.close()
    finally:
        db.close()


def test_manual_magnet_wait_expire_skips_without_dead_link(client, app, monkeypatch):
    login(client)
    _enable_debrid(app)
    magnet = "magnet:?xt=urn:btih:0909090909090909090909090909090909090909"
    created = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Série test", "year": 2026},
        headers=csrf(client),
    )
    media_id = created.json()["id"]
    item_rsp = client.post(
        f"/api/v1/media/{media_id}/items",
        json={"season": 1, "episode": 2},
        headers=csrf(client),
    )
    item_id = item_rsp.json()["id"]
    db = app.state.session_factory()
    try:
        provider_id = db.scalar(select(Provider.id).where(Provider.kind == "source", Provider.type == "manual"))
        link_row = Link(
            media_item_id=item_id,
            provider_id=provider_id,
            kind="magnet",
            url=magnet,
            hoster=None,
            external_id="manual:magnet-test",
            discovered_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        download = Download(media_item_id=item_id, state="pending", created_at=created_at)
        db.add_all([link_row, download])
        db.commit()
        download_id = download.id
        debrid = _NeverReadyDebrid()
        start = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
        tick(db, app.state.settings, now=start, debrid=debrid, downloader=FakeDownloader())
        row = db.get(Download, download_id)
        assert is_magnet_wait(row)
        row.attempt_started_at = start - timedelta(seconds=700)
        db.commit()
        tick(db, app.state.settings, now=start + timedelta(hours=1), debrid=debrid, downloader=FakeDownloader())
        magnet_link = db.get(Link, link_row.id)
        assert not magnet_link.dead
        row = db.get(Download, download_id)
        assert link_row.id in (row.skipped_link_ids or [])
    finally:
        db.close()


def test_two_successive_magnets_reset_wait_budget(client, app, monkeypatch):
    login(client)
    _enable_debrid(app)
    media = _series_demo(client, app, order="comet")
    link = _link(client, media["id"], "https://rapidgator.net/file/ignore", 1)
    db0 = app.state.session_factory()
    try:
        for row in db0.scalars(select(Link)).all():
            db0.delete(row)
        db0.commit()
    finally:
        db0.close()
    _stub_two_comet_magnets(monkeypatch)
    debrid = _NeverReadyDebrid()
    start = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=start, debrid=debrid, downloader=FakeDownloader())
        row = db.get(Download, link["download_id"])
        assert row.debrid_magnet_id == "99"
        first_started = row.attempt_started_at
        row.attempt_started_at = start - timedelta(seconds=700)
        db.commit()
        tick(db, app.state.settings, now=start + timedelta(minutes=15), debrid=debrid, downloader=FakeDownloader())
        row = db.get(Download, link["download_id"])
        if is_magnet_wait(row):
            assert row.debrid_magnet_id == "100"
            assert _aware(row.attempt_started_at) > _aware(first_started)
        else:
            assert len(debrid.uploaded) == 2
    finally:
        db.close()


def _stub_comet_hoster_only(monkeypatch):
    hoster = LinkCandidate(
        url="https://rapidgator.net/file/comet-only",
        kind="ddl_hoster",
        hoster="rapidgator",
        quality="1080p",
        language="MULTI",
        size=PLAUSIBLE_VIDEO_BYTES,
        filename="Exemple.S01E01.mkv",
        season=1,
        episode=1,
        external_id="comet:rapidgator:only",
    )

    class _Source:
        type = "comet"

        def links_for(self, query, preferences):
            del query, preferences
            return [hoster]

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "mediatracker.providers.sources.loading.episode_source",
        lambda name, settings, *, db=None, base_url=None, base_urls=None, transport=None: _Source(),
    )


def test_comet_wait_always_stores_magnet_link(client, app, monkeypatch):
    login(client)
    _enable_debrid(app)
    media = _series_demo(client, app, order="comet")
    for stale in app.state.session_factory().scalars(select(Link)).all():
        app.state.session_factory().delete(stale)
    app.state.session_factory().commit()
    _stub_source(monkeypatch)
    debrid = _NeverReadyDebrid()
    db = app.state.session_factory()
    try:
        manual_trap = _link(client, media["id"], "https://rapidgator.net/file/other", 1)
        row = db.get(Download, manual_trap["download_id"])
        row.link_id = manual_trap["id"]
        db.commit()
        now = datetime(2026, 10, 12, tzinfo=timezone.utc)
        tick(db, app.state.settings, now=now, debrid=debrid, downloader=FakeDownloader())
        row = db.get(Download, manual_trap["download_id"])
        assert is_magnet_wait(row)
        assert row.link_id != manual_trap["id"]
        stored = db.get(Link, row.link_id)
        assert stored is not None
        assert stored.kind == "magnet"
        assert stored.url.startswith("magnet:")
    finally:
        db.close()


def test_comet_no_magnet_link_before_real_wait(client, app, monkeypatch):
    login(client)
    _enable_debrid(app)
    media = _series_demo(client, app, order="comet")
    _stub_source(monkeypatch)
    debrid = _NeverReadyDebrid()
    start = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        comet_pid = db.scalar(select(Provider.id).where(Provider.kind == "source", Provider.type == "comet"))
        before = db.scalar(select(Link.id).where(Link.provider_id == comet_pid, Link.kind == "magnet").limit(1))
        assert before is None
        boot = _link(client, media["id"], "https://rapidgator.net/file/ignore", 1)
        tick(db, app.state.settings, now=start, debrid=debrid, downloader=FakeDownloader())
        row = db.get(Download, boot["download_id"])
        assert is_magnet_wait(row)
        stored = db.get(Link, row.link_id)
        assert stored is not None and stored.kind == "magnet"
    finally:
        db.close()


def test_resume_non_video_rejects_magnet_durably(client, app, monkeypatch):
    login(client)
    _enable_debrid(app)
    media = _series_demo(client, app, order="comet")
    boot = _link(client, media["id"], "https://rapidgator.net/file/ignore", 1)
    _stub_source(monkeypatch)
    debrid = _NeverReadyDebrid()
    start = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=start, debrid=debrid, downloader=FakeDownloader())
        row = db.get(Download, boot["download_id"])
        magnet_link_id = row.link_id
        assert is_magnet_wait(row)

        def ready_status(magnet_id: str) -> MagnetSnapshot:
            files = [MagnetFile(0, "Exemple.S01E01.mkv", 40, "https://alldebrid.com/f/demo")]
            return MagnetSnapshot(magnet_id, True, files)

        debrid.magnet_status = ready_status  # type: ignore[method-assign]
        debrid.unlock = lambda url: DirectLink(  # type: ignore[method-assign, return-value]
            url="https://cdn.example/bad.bin",
            filename="Exemple.S01E01.mkv",
            size=40,
        )
        monkeypatch.setattr(
            "mediatracker.core.pipeline.unlock._probed_direct",
            lambda url, size: "Fichier non vidéo.",
        )
        tick(db, app.state.settings, now=start + timedelta(minutes=5), debrid=debrid, downloader=FakeDownloader())
        row = db.get(Download, boot["download_id"])
        assert not is_magnet_wait(row)
        magnet_link = db.get(Link, magnet_link_id)
        assert magnet_link.dead
        assert magnet_link.last_error
        assert magnet_link_id in (row.skipped_link_ids or [])
        assert row.link_id is None
    finally:
        db.close()


def test_resume_transient_debrid_error_keeps_wait_budget(client, app, monkeypatch):
    login(client)
    _enable_debrid(app)
    media = _series_demo(client, app, order="comet")
    boot = _link(client, media["id"], "https://rapidgator.net/file/ignore", 1)
    _stub_source(monkeypatch)
    debrid = _NeverReadyDebrid()
    fail_once = {"pending": True}
    start = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=start, debrid=debrid, downloader=FakeDownloader())
        row = db.get(Download, boot["download_id"])
        assert is_magnet_wait(row)
        budget_start = row.attempt_started_at
        uploads = len(debrid.uploaded)
        status_before = debrid.status_calls

        def flaky_status(magnet_id: str) -> MagnetSnapshot:
            debrid.status_calls += 1
            if fail_once["pending"]:
                fail_once["pending"] = False
                raise AllDebridError("TRANSPORT", "panne réseau", retryable=True)
            return MagnetSnapshot(magnet_id, False, [])

        debrid.magnet_status = flaky_status  # type: ignore[method-assign]
        tick(db, app.state.settings, now=start + timedelta(seconds=35), debrid=debrid, downloader=FakeDownloader())
        row = db.get(Download, boot["download_id"])
        assert is_magnet_wait(row)
        assert _aware(row.attempt_started_at) == _aware(budget_start)
        assert len(debrid.uploaded) == uploads
        assert debrid.status_calls == status_before + 1
    finally:
        db.close()


def test_transient_resume_errors_only_poll_once_per_interval(client, app, monkeypatch):
    login(client)
    _enable_debrid(app)
    media = _series_demo(client, app, order="comet")
    boot = _link(client, media["id"], "https://rapidgator.net/file/ignore", 1)
    _stub_source(monkeypatch)
    debrid = _NeverReadyDebrid()
    start = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=start, debrid=debrid, downloader=FakeDownloader())
        row = db.get(Download, boot["download_id"])
        assert is_magnet_wait(row)
        baseline = debrid.status_calls

        def fail_status(magnet_id: str) -> MagnetSnapshot:
            debrid.status_calls += 1
            raise AllDebridError("TRANSPORT", "panne réseau", retryable=True)

        debrid.magnet_status = fail_status  # type: ignore[method-assign]
        first_error = start + timedelta(seconds=35)
        tick(db, app.state.settings, now=first_error, debrid=debrid, downloader=FakeDownloader())
        row = db.get(Download, boot["download_id"])
        assert is_magnet_wait(row)
        after_first = debrid.status_calls
        assert after_first == baseline + 1
        expected_next = first_error + timedelta(
            seconds=app.state.settings.alldebrid_magnet_poll_seconds
        )
        assert _aware(row.next_retry_at) == expected_next
        too_soon = first_error + timedelta(seconds=5)
        tick(db, app.state.settings, now=too_soon, debrid=debrid, downloader=FakeDownloader())
        assert debrid.status_calls == after_first
        second_due = first_error + timedelta(seconds=35)
        tick(db, app.state.settings, now=second_due, debrid=debrid, downloader=FakeDownloader())
        assert debrid.status_calls == after_first + 1
    finally:
        db.close()


def test_magnet_poll_interval_blocks_status_until_due(client, app, monkeypatch):
    login(client)
    _enable_debrid(app)
    media = _series_demo(client, app, order="comet")
    boot = _link(client, media["id"], "https://rapidgator.net/file/ignore", 1)
    _stub_source(monkeypatch)
    debrid = _NeverReadyDebrid()
    start = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=start, debrid=debrid, downloader=FakeDownloader())
        row = db.get(Download, boot["download_id"])
        assert is_magnet_wait(row)
        calls_after_wait = debrid.status_calls
        tick(db, app.state.settings, now=start + timedelta(seconds=15), debrid=debrid, downloader=FakeDownloader())
        assert debrid.status_calls == calls_after_wait
        tick(db, app.state.settings, now=start + timedelta(seconds=35), debrid=debrid, downloader=FakeDownloader())
        assert debrid.status_calls > calls_after_wait
    finally:
        db.close()


def test_success_after_wait_resets_attempt_started_at(client, app, monkeypatch):
    login(client)
    _enable_debrid(app)
    media = _series_demo(client, app, order="comet")
    link = _link(client, media["id"], "https://rapidgator.net/file/abc", 1)
    _stub_source(monkeypatch)
    debrid = _NeverReadyDebrid()
    wait_start = datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc)
    success_at = wait_start + timedelta(minutes=5)
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=wait_start, debrid=debrid, downloader=FakeDownloader())
        row = db.get(Download, link["download_id"])
        assert is_magnet_wait(row)

        def ready_status(magnet_id: str) -> MagnetSnapshot:
            files = [MagnetFile(0, "Exemple.S01E01.mkv", 40, "https://alldebrid.com/f/demo")]
            return MagnetSnapshot(magnet_id, True, files)

        debrid.magnet_status = ready_status  # type: ignore[method-assign]

        def unlock_ok(url: str) -> DirectLink:
            if url.startswith("https://alldebrid.com/"):
                return DirectLink(url="https://cdn.example/comet.mkv", filename="Exemple.S01E01.mkv", size=40)
            return FakeDebrid.unlock(debrid, url)

        debrid.unlock = unlock_ok  # type: ignore[method-assign]
        tick(db, app.state.settings, now=success_at, debrid=debrid, downloader=FakeDownloader())
        row = db.get(Download, link["download_id"])
        assert _aware(row.attempt_started_at) == success_at
    finally:
        db.close()


def test_pass_under_30s_without_magnet_status(client, app, monkeypatch):
    login(client)
    _enable_debrid(app)
    media = _series_demo(client, app, order="comet")
    _link(client, media["id"], "https://rapidgator.net/file/ignore", 1)
    _stub_comet_hoster_only(monkeypatch)
    debrid = _NeverReadyDebrid()
    db = app.state.session_factory()
    try:
        before = time.perf_counter()
        tick(
            db,
            app.state.settings,
            now=datetime(2026, 10, 12, tzinfo=timezone.utc),
            debrid=debrid,
            downloader=FakeDownloader(),
        )
        elapsed = time.perf_counter() - before
        assert elapsed < 30
        assert debrid.status_calls == 0
        assert debrid.uploaded == []
    finally:
        db.close()


def test_default_blocking_wait_is_nonblocking():
    debrid = _NeverReadyDebrid()
    result = claim_cached_magnet(
        debrid,
        "magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        file_index=0,
        filename="Exemple.S01E01.mkv",
    )
    assert isinstance(result, MagnetClaimPending)
    assert debrid.status_calls == 1