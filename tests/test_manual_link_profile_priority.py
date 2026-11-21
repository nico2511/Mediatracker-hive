"""Lien manuel : priorité sur le filtre profil et passage dédié dans la file."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import select
from tests.conftest import csrf, login, profile_compliant_release_filename
from tests.test_pipeline import FakeDebrid

from mediatracker.core.pipeline.source_attempt_journal import MANUAL_LINK_USED_JOURNAL
from mediatracker.core.pipeline.unlock import _from_manual_links
from mediatracker.core.profile_attempt_adapters import (
    choose_link_for_profile_attempt,
    rank_link_for_profile_attempt,
)
from mediatracker.core.quality_profile import default_profile, detect_language, normalize_resolution
from mediatracker.db.models import Download, Event, Link, Media, MediaItem, Provider

NOW = datetime(2026, 11, 21, tzinfo=timezone.utc)
MANUAL_STUB = Provider(id=99, kind="source", type="manual", name="Manual", enabled=True)


def _manual_provider_id(db) -> int:
    from sqlalchemy import select

    row = db.scalar(select(Provider).where(Provider.kind == "source", Provider.type == "manual"))
    assert row is not None
    return row.id


@pytest.fixture
def db(client):
    session = client.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


def test_manual_hd_link_ranked_despite_strict_1080p_profile():
    """Reproduction : HD + profil VF/Multi 1080p → refusé sans priorité manuelle."""
    profile = default_profile()
    link = Link(
        provider_id=MANUAL_STUB.id,
        provider=MANUAL_STUB,
        quality="HD",
        language="MULTI",
        hoster="rapidgator",
        kind="ddl_protected",
        url="https://dl-protect.link/demo",
        priority=10,
        filename="Exemple.Demo.2099.MULTi.HD.WEB.x264-GROUP.mkv",
        external_id="manual:demo",
    )
    assert rank_link_for_profile_attempt(link, profile=profile) is not None
    link.provider = None
    link.provider_id = 2
    assert rank_link_for_profile_attempt(link, profile=profile) is None


def test_choose_manual_over_profile_compliant_wawacity_link():
    profile = default_profile()
    manual = Link(
        id=1,
        provider_id=MANUAL_STUB.id,
        provider=MANUAL_STUB,
        quality="HD",
        language="MULTI",
        hoster="blockedhost",
        kind="ddl_protected",
        url="https://dl-protect.link/manual",
        priority=5,
        filename="Exemple.Demo.2099.MULTi.HD.WEB.x264-GROUP.mkv",
        external_id="manual:one",
    )
    compliant = Link(
        id=2,
        quality="1080p",
        language="MULTI",
        hoster="rapidgator",
        kind="ddl_protected",
        url="https://dl-protect.link/wawa",
        priority=100,
        filename=profile_compliant_release_filename(season=1, episode=1),
        external_id="wawa:one",
    )
    chosen = choose_link_for_profile_attempt(
        ["rapidgator"],
        links=[compliant, manual],
        profile=profile,
        skipped_link_ids=set(),
    )
    assert chosen is manual


def test_create_link_prefers_filename_quality_over_fiche_default(client):
    login(client)
    media = client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Fiche Démo", "year": 2026, "pref_quality": "HD", "pref_language": "VFF"},
        headers=csrf(client),
    )
    assert media.status_code == 201
    media_id = media.json()["id"]
    name = "Exemple.Demo.2099.MULTi.1080p.WEB.x264-GROUP.mkv"
    linked = client.post(
        "/api/v1/links",
        json={
            "media_id": media_id,
            "url": f"https://rapidgator.net/file/{name}",
            "start": False,
        },
        headers=csrf(client),
    )
    assert linked.status_code == 201, linked.text
    body = linked.json()
    assert body["quality"] == "1080p"
    assert body["language"] == "MULTI"


def test_manual_link_used_journal_and_log_sans_restriction(caplog, client, app):
    login(client)
    media_rsp = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Log Démo", "year": 2026},
        headers=csrf(client),
    )
    media_id = media_rsp.json()["id"]
    item_rsp = client.post(
        f"/api/v1/media/{media_id}/items",
        json={"season": 1, "episode": 1},
        headers=csrf(client),
    )
    item_id = item_rsp.json()["id"]
    db = app.state.session_factory()
    try:
        provider_id = _manual_provider_id(db)
        link = Link(
            media_item_id=item_id,
            provider_id=provider_id,
            kind="ddl_hoster",
            url="https://rapidgator.net/file/log-demo.html",
            hoster="rapidgator",
            quality="HD",
            language="MULTI",
            external_id="manual:log-demo",
            discovered_at=NOW,
        )
        download = Download(media_item_id=item_id, state="pending", created_at=NOW)
        db.add_all([link, download])
        db.commit()
        media = db.get(Media, media_id)
        item = db.get(MediaItem, item_id)
        row = db.get(Download, download.id)
        caplog.set_level(logging.INFO, logger="mediatracker.core.pipeline.manual_link")
        with patch("mediatracker.core.pipeline.unlock._commit", lambda *_args, **_kwargs: None):
            outcome = _from_manual_links(
                db,
                row,
                item,
                media,
                NOW,
                ["rapidgator"],
                None,
                FakeDebrid(),
                app.state.settings,
                [],
            )
        assert outcome is not None
        event = db.scalar(
            select(Event).where(
                Event.type == "download.manual_link",
                Event.media_id == media_id,
            )
        )
        assert event is not None
        assert "sans restriction" in (event.payload or {}).get("message", "")
        assert event.payload.get("message") == MANUAL_LINK_USED_JOURNAL
        assert any(
            "sans restriction" in record.message and "hébergeur=rapidgator" in record.message
            for record in caplog.records
        )
        joined = "\n".join(record.message for record in caplog.records)
        assert "rapidgator.net" not in joined
        assert "https://" not in joined
    finally:
        db.close()


def test_manual_link_failed_logs_warning_sans_restriction(caplog, client, app):
    login(client)
    media_rsp = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Log Échec", "year": 2026},
        headers=csrf(client),
    )
    media_id = media_rsp.json()["id"]
    item_rsp = client.post(
        f"/api/v1/media/{media_id}/items",
        json={"season": 1, "episode": 1},
        headers=csrf(client),
    )
    item_id = item_rsp.json()["id"]
    db = app.state.session_factory()
    try:
        provider_id = _manual_provider_id(db)
        link = Link(
            media_item_id=item_id,
            provider_id=provider_id,
            kind="ddl_hoster",
            url="https://rapidgator.net/file/dead-demo.html",
            hoster="rapidgator",
            quality="HD",
            language="MULTI",
            external_id="manual:dead-demo",
            discovered_at=NOW,
        )
        download = Download(media_item_id=item_id, state="pending", created_at=NOW)
        db.add_all([link, download])
        db.commit()
        media = db.get(Media, media_id)
        item = db.get(MediaItem, item_id)
        row = db.get(Download, download.id)
        debrid = FakeDebrid()
        debrid.mode = "dead"
        caplog.set_level(logging.WARNING, logger="mediatracker.core.pipeline.manual_link")
        with patch("mediatracker.core.pipeline.unlock._commit", lambda *_args, **_kwargs: None):
            outcome = _from_manual_links(
                db,
                row,
                item,
                media,
                NOW,
                ["rapidgator"],
                None,
                debrid,
                app.state.settings,
                [],
            )
        assert outcome is None
        assert any(
            record.levelname == "WARNING" and "sans restriction" in record.message
            for record in caplog.records
        )
    finally:
        db.close()


def test_from_manual_links_journals_explicit_success(client, app):
    login(client)
    media_rsp = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Journal Démo", "year": 2026},
        headers=csrf(client),
    )
    media_id = media_rsp.json()["id"]
    item_rsp = client.post(
        f"/api/v1/media/{media_id}/items",
        json={"season": 1, "episode": 1},
        headers=csrf(client),
    )
    item_id = item_rsp.json()["id"]
    db = app.state.session_factory()
    try:
        provider_id = _manual_provider_id(db)
        link = Link(
            media_item_id=item_id,
            provider_id=provider_id,
            kind="ddl_hoster",
            url="https://rapidgator.net/file/journal-demo.html",
            hoster="rapidgator",
            quality="HD",
            language="MULTI",
            filename="Exemple.Demo.2099.MULTi.HD.WEB.x264-GROUP.mkv",
            priority=1,
            external_id="manual:journal-demo",
            discovered_at=NOW,
        )
        download = Download(media_item_id=item_id, state="pending", created_at=NOW)
        db.add_all([link, download])
        db.commit()
        media = db.get(Media, media_id)
        item = db.get(MediaItem, item_id)
        row = db.get(Download, download.id)
        journal: list[str] = []

        def capture_used(*args, **kwargs):
            del args, kwargs
            journal.append("used")

        with (
            patch("mediatracker.core.pipeline.unlock._commit", lambda *_args, **_kwargs: None),
            patch(
                "mediatracker.core.pipeline.unlock.record_manual_link_used",
                side_effect=capture_used,
            ),
        ):
            outcome = _from_manual_links(
                db,
                row,
                item,
                media,
                NOW,
                ["rapidgator"],
                None,
                FakeDebrid(),
                app.state.settings,
                [],
            )
        assert outcome is not None
        assert journal == ["used"]
    finally:
        db.close()


def test_filename_parsing_helpers():
    name = profile_compliant_release_filename()
    assert normalize_resolution(name) == "1080p"
    assert detect_language(name) == "MULTI"