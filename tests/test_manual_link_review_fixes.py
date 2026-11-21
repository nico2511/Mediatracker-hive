"""Revue PR #436 : page manuelle (morts), retry manuel → cascade, magnet collé."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import func, select
from tests.conftest import connect_alldebrid_for_tests, csrf, login
from tests.test_pipeline import FakeDebrid

from mediatracker.core.magnet_claim import ClaimedMagnet
from mediatracker.core.pipeline import jobs
from mediatracker.core.pipeline.manual_source_page import (
    link_for_manual_page_candidate,
    pick_manual_source_page_link,
)
from mediatracker.core.pipeline.unlock import _from_manual_links
from mediatracker.core.profile_attempt_orchestration import load_profile_chain
from mediatracker.core.profile_fallback import (
    GLOBAL_ALLOW_FALLBACK_KEY,
    PROFILE_FALLBACK_ORDER_KEY,
    format_profile_fallback_order,
)
from mediatracker.core.quality_profile import ensure_default_profile
from mediatracker.db.models import Download, Link, Media, MediaItem, Provider, QualityProfile, Setting, SourceBinding
from mediatracker.providers.base import DirectLink, LinkCandidate

NOW = datetime(2026, 11, 21, tzinfo=timezone.utc)


def _upsert_setting(db, key: str, value: str) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value, is_secret=False))
    else:
        row.value = value
    db.commit()


def _hd_episode_one() -> LinkCandidate:
    return LinkCandidate(
        url="https://dl-protect.link/rg1",
        kind="ddl_protected",
        hoster="rapidgator",
        quality="HD",
        language="VFF",
        season=1,
        episode=1,
        external_id="wawa:rg1",
    )


@pytest.fixture
def db_session(client):
    session = client.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


def test_link_for_nested_integrity_conflict_keeps_session_no_url_in_log(caplog, db_session):
    caplog.set_level(logging.INFO, logger="mediatracker.core.pipeline.manual_source_page")
    provider = db_session.scalar(select(Provider).where(Provider.type == "wawacity"))
    media = Media(type="series", title="Savepoint", year=2026)
    db_session.add(media)
    db_session.flush()
    item = MediaItem(media_id=media.id, season=1, episode=1)
    db_session.add(item)
    db_session.flush()
    candidate = _hd_episode_one()
    candidate = LinkCandidate(
        url="https://leak.example/should-not-appear",
        kind=candidate.kind,
        hoster=candidate.hoster,
        quality=candidate.quality,
        language=candidate.language,
        season=candidate.season,
        episode=candidate.episode,
        external_id="wawa:savepoint-dup",
    )
    existing = Link(
        media_item_id=item.id,
        provider_id=provider.id,
        kind="ddl_protected",
        url="https://leak.example/existing",
        hoster="rapidgator",
        external_id=candidate.external_id,
        discovered_at=NOW,
    )
    db_session.add(existing)
    db_session.commit()
    real_scalar = db_session.scalar
    seen = {"missed": False}

    def scalar(stmt, *args, **kwargs):
        statement = str(stmt)
        if "link" in statement.lower() and "external_id" in statement.lower() and not seen["missed"]:
            seen["missed"] = True
            return None
        return real_scalar(stmt, *args, **kwargs)

    with patch.object(db_session, "scalar", side_effect=scalar):
        resolved = link_for_manual_page_candidate(
            db_session, item, candidate, provider.id, ["rapidgator"], NOW
        )
    assert resolved is not None
    assert resolved.id == existing.id
    probe = Media(type="film", title="Post conflit", year=2027)
    db_session.add(probe)
    db_session.flush()
    joined = caplog.text.lower()
    assert "http" not in joined
    assert "leak.example" not in joined
    assert "integrityerror" in joined


def test_begin_clears_pass_error_before_manual_link_attempt(client, app, db_session, settings, monkeypatch):
    connect_alldebrid_for_tests(app)
    login(client)
    from tests.test_pipeline import FakeDownloader

    from mediatracker.core.pipeline import queue as download_queue

    media = Media(type="series", title="Erreur pass", year=2026, followed=True)
    db_session.add(media)
    db_session.flush()
    item = MediaItem(media_id=media.id, season=1, episode=1, state="missing")
    db_session.add(item)
    db_session.flush()
    download = Download(
        media_item_id=item.id,
        state="pending",
        created_at=NOW,
        error_code="NO_LINK",
        error="ancienne erreur cascade",
    )
    db_session.add(download)
    db_session.commit()

    errors_at_manual: list[tuple[str | None, str | None]] = []

    def capture_manual(*_args, **kwargs):
        row = kwargs.get("row") if "row" in kwargs else _args[1]
        errors_at_manual.append((row.error_code, row.error))
        return None

    monkeypatch.setattr("mediatracker.core.pipeline.unlock._from_manual_links", capture_manual)
    monkeypatch.setattr("mediatracker.core.pipeline.unlock._from_manual_source_page", lambda *_a, **_k: None)
    monkeypatch.setattr(download_queue, "magnet_cache_first", lambda *_a, **_k: None)
    monkeypatch.setattr(download_queue, "load_known_files", lambda *_a, **_k: ([], False))
    monkeypatch.setattr(download_queue, "_claim_library_video", lambda *_a, **_k: None)
    monkeypatch.setattr(download_queue, "_already_local", lambda *_a, **_k: False)
    monkeypatch.setattr(download_queue, "_usable_download_sources", lambda *_a, **_k: [])
    monkeypatch.setattr(download_queue, "peek_next_download_profile_repli", lambda *_a, **_k: None)
    monkeypatch.setattr("mediatracker.core.pipeline.unlock._from_named_source", lambda *_a, **_k: None)

    row = db_session.get(Download, download.id)
    download_queue._begin(
        db_session,
        row,
        NOW,
        [],
        None,
        FakeDebrid(),
        None,
        FakeDownloader(),
        settings,
        lambda _p: 10**12,
        [],
    )
    assert errors_at_manual
    assert errors_at_manual[0] == (None, None)


def test_link_for_manual_page_ignores_link_owned_by_other_episode(db_session):
    provider = db_session.scalar(select(Provider).where(Provider.type == "wawacity"))
    media = Media(type="series", title="Autre slot", year=2026)
    db_session.add(media)
    db_session.flush()
    item_a = MediaItem(media_id=media.id, season=1, episode=1)
    item_b = MediaItem(media_id=media.id, season=1, episode=2)
    db_session.add_all([item_a, item_b])
    db_session.flush()
    candidate = _hd_episode_one()
    owned = Link(
        media_item_id=item_b.id,
        provider_id=provider.id,
        kind="ddl_protected",
        url=candidate.url,
        hoster="rapidgator",
        external_id=candidate.external_id,
        discovered_at=NOW,
    )
    db_session.add(owned)
    db_session.commit()
    assert link_for_manual_page_candidate(db_session, item_a, candidate, provider.id, ["rapidgator"], NOW) is None
    assert owned.media_item_id == item_b.id


def test_link_for_manual_page_reuses_dead_row_without_duplicate(db_session):
    provider = db_session.scalar(select(Provider).where(Provider.type == "wawacity"))
    media = Media(type="series", title="Lien mort", year=2026)
    db_session.add(media)
    db_session.flush()
    item = MediaItem(media_id=media.id, season=1, episode=1)
    db_session.add(item)
    db_session.flush()
    candidate = _hd_episode_one()
    dead = Link(
        media_item_id=item.id,
        provider_id=provider.id,
        kind="ddl_protected",
        url=candidate.url,
        hoster="rapidgator",
        external_id=candidate.external_id,
        dead=True,
        last_error="LINK_DOWN",
        discovered_at=NOW,
    )
    db_session.add(dead)
    db_session.commit()
    row = link_for_manual_page_candidate(db_session, item, candidate, provider.id, ["rapidgator"], NOW)
    assert row.id == dead.id
    count = db_session.scalar(
        select(func.count()).select_from(Link).where(
            Link.provider_id == provider.id,
            Link.external_id == candidate.external_id,
        )
    )
    assert count == 1


def test_pick_dead_only_on_main_tries_repli_when_other_live(client, db_session, settings):
    login(client)
    ensure_default_profile(db_session)
    repli = db_session.scalar(select(QualityProfile).where(QualityProfile.name == "720p VF"))
    _upsert_setting(db_session, PROFILE_FALLBACK_ORDER_KEY, format_profile_fallback_order([repli.id]))
    _upsert_setting(db_session, GLOBAL_ALLOW_FALLBACK_KEY, "true")
    provider = db_session.scalar(select(Provider).where(Provider.type == "wawacity"))
    media = Media(type="series", title="Repli vivant", year=2023)
    db_session.add(media)
    db_session.flush()
    item = MediaItem(media_id=media.id, season=1, episode=1)
    db_session.add(item)
    db_session.flush()
    dead_main = LinkCandidate(
        url="https://dl-protect.link/main",
        kind="ddl_protected",
        hoster="rapidgator",
        quality="1080p",
        language="VF",
        season=1,
        episode=1,
        external_id="wawa:1080-dead",
    )
    live_repli = _hd_episode_one()
    db_session.add(
        Link(
            media_item_id=item.id,
            provider_id=provider.id,
            kind="ddl_protected",
            url=dead_main.url,
            hoster="rapidgator",
            external_id=dead_main.external_id,
            dead=True,
            discovered_at=NOW,
        )
    )
    db_session.commit()
    chain = load_profile_chain(db_session, media, settings)
    pick = pick_manual_source_page_link(
        db_session,
        provider.id,
        chain.profiles,
        [dead_main, live_repli],
        item,
        ["rapidgator"],
    )
    assert pick is not None
    ordered, profile, rank = pick
    assert profile.name == "720p VF"
    assert rank == 2
    assert ordered[0].external_id == live_repli.external_id


def test_manual_link_exhausted_retries_yield_cascade_not_fail(client, app):
    login(client)
    connect_alldebrid_for_tests(app)
    media_rsp = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Retry Démo", "year": 2026},
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
        provider_id = db.scalar(
            select(Provider.id).where(Provider.kind == "source", Provider.type == "manual")
        )
        link = Link(
            media_item_id=item_id,
            provider_id=provider_id,
            kind="ddl_hoster",
            url="https://rapidgator.net/file/retry-demo.html",
            hoster="rapidgator",
            quality="HD",
            language="MULTI",
            external_id="manual:retry-demo",
            discovered_at=NOW,
        )
        download = Download(media_item_id=item_id, state="pending", created_at=NOW, attempts=jobs.MAX_ATTEMPTS - 1)
        db.add_all([link, download])
        db.commit()
        media = db.get(Media, media_id)
        item = db.get(MediaItem, item_id)
        row = db.get(Download, download.id)
        debrid = FakeDebrid()
        debrid.mode = "unavailable"
        with patch("mediatracker.core.pipeline.unlock._commit", lambda *_a, **_k: None):
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
        assert row.state != "failed"
    finally:
        db.close()


def test_begin_prefers_pasted_direct_link_over_manual_page(client, app, db_session, settings, monkeypatch):
    connect_alldebrid_for_tests(app)
    login(client)
    from tests.test_pipeline import FakeDownloader

    from mediatracker.core.pipeline import queue as download_queue

    media = Media(type="series", title="Priorité direct", year=2026, followed=True)
    db_session.add(media)
    db_session.flush()
    item = MediaItem(media_id=media.id, season=1, episode=1, state="missing")
    db_session.add(item)
    db_session.flush()
    manual_provider = db_session.scalar(select(Provider).where(Provider.type == "manual"))
    wawa_provider = db_session.scalar(select(Provider).where(Provider.type == "wawacity"))
    pasted = Link(
        media_item_id=item.id,
        provider_id=manual_provider.id,
        kind="ddl_hoster",
        url="https://rapidgator.net/file/pasted-wins.html",
        hoster="rapidgator",
        external_id="manual:pasted-wins",
        discovered_at=NOW,
    )
    db_session.add(pasted)
    db_session.add(
        SourceBinding(
            media_id=media.id,
            provider_id=wawa_provider.id,
            external_ref="/page-demo",
            url="https://wawa.example/page-demo",
            path="/page-demo",
            path_manual=True,
            active=True,
            season=1,
        )
    )
    download = Download(media_item_id=item.id, state="pending", created_at=NOW)
    db_session.add(download)
    db_session.commit()

    call_order: list[str] = []

    def manual_wins(*_args, **_kwargs):
        call_order.append("manual")
        return (pasted, "https://cdn.example/pasted.mkv", "Pasted.mkv", 48, None)

    def page_if_reached(*_args, **_kwargs):
        call_order.append("page")
        raise AssertionError("La page manuelle ne doit pas être appelée si un lien direct gagne.")

    monkeypatch.setattr("mediatracker.core.pipeline.unlock._from_manual_links", manual_wins)
    monkeypatch.setattr("mediatracker.core.pipeline.unlock._from_manual_source_page", page_if_reached)
    monkeypatch.setattr(download_queue, "magnet_cache_first", lambda *_a, **_k: None)
    monkeypatch.setattr(download_queue, "load_known_files", lambda *_a, **_k: ([], False))
    monkeypatch.setattr(download_queue, "_claim_library_video", lambda *_a, **_k: None)
    monkeypatch.setattr(download_queue, "_already_local", lambda *_a, **_k: False)

    row = db_session.get(Download, download.id)
    outcome = download_queue._begin(
        db_session,
        row,
        NOW,
        ["rapidgator"],
        None,
        FakeDebrid(),
        None,
        FakeDownloader(),
        settings,
        lambda _p: 10**12,
        [],
    )
    assert outcome == "started"
    assert call_order == ["manual"]


def test_begin_calls_manual_page_only_after_direct_manual_misses(client, app, db_session, settings, monkeypatch):
    connect_alldebrid_for_tests(app)
    login(client)
    from tests.test_pipeline import FakeDownloader

    from mediatracker.core.pipeline import queue as download_queue

    media = Media(type="series", title="Ordre appels", year=2026, followed=True)
    db_session.add(media)
    db_session.flush()
    item = MediaItem(media_id=media.id, season=1, episode=1, state="missing")
    db_session.add(item)
    db_session.flush()
    download = Download(media_item_id=item.id, state="pending", created_at=NOW)
    db_session.add(download)
    db_session.commit()

    call_order: list[str] = []

    monkeypatch.setattr(
        "mediatracker.core.pipeline.unlock._from_manual_links",
        lambda *_a, **_k: call_order.append("manual") or None,
    )
    monkeypatch.setattr(
        "mediatracker.core.pipeline.unlock._from_manual_source_page",
        lambda *_a, **_k: call_order.append("page") or None,
    )
    monkeypatch.setattr(download_queue, "magnet_cache_first", lambda *_a, **_k: None)
    monkeypatch.setattr(download_queue, "load_known_files", lambda *_a, **_k: ([], False))
    monkeypatch.setattr(download_queue, "_claim_library_video", lambda *_a, **_k: None)
    monkeypatch.setattr(download_queue, "_already_local", lambda *_a, **_k: False)
    monkeypatch.setattr(download_queue, "_usable_download_sources", lambda *_a, **_k: [])
    monkeypatch.setattr(
        "mediatracker.core.pipeline.unlock._from_named_source",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(download_queue, "peek_next_download_profile_repli", lambda *_a, **_k: None)

    row = db_session.get(Download, download.id)
    download_queue._begin(
        db_session,
        row,
        NOW,
        [],
        None,
        FakeDebrid(),
        None,
        FakeDownloader(),
        settings,
        lambda _p: 10**12,
        [],
    )
    assert call_order == ["manual", "page"]


def test_manual_pasted_magnet_bypass_profile(client, app, monkeypatch):
    login(client)
    connect_alldebrid_for_tests(app)
    magnet = "magnet:?xt=urn:btih:0102030405060708090a0b0c0d0e0f0102030405&dn=Episode.mkv"
    media_rsp = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Magnet Démo", "year": 2026},
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
        provider_id = db.scalar(
            select(Provider.id).where(Provider.kind == "source", Provider.type == "manual")
        )
        link = Link(
            media_item_id=item_id,
            provider_id=provider_id,
            kind="magnet",
            url=magnet,
            hoster=None,
            external_id="manual:magnet-demo",
            discovered_at=NOW,
        )
        download = Download(media_item_id=item_id, state="pending", created_at=NOW)
        db.add_all([link, download])
        db.commit()
        media = db.get(Media, media_id)
        item = db.get(MediaItem, item_id)
        row = db.get(Download, download.id)

        class MagnetDebrid(FakeDebrid):
            def upload_magnet(self, url: str):
                self.seen.append(url)
                return type("MagnetUpload", (), {"id": "mag-1", "ready": True, "files": []})()

            def magnet_cache(self, magnets):
                return []

        direct = DirectLink(url="https://cdn.example/magnet.mkv", filename="Episode.mkv", size=48)
        claimed = ClaimedMagnet(direct, "mag-1", "https://alldebrid.invalid/f/1")

        def fake_claim(debrid, magnet_url, **kwargs):
            del kwargs
            assert magnet_url == magnet
            return claimed

        monkeypatch.setattr("mediatracker.core.magnet_claim.claim_cached_magnet", fake_claim)
        with patch("mediatracker.core.pipeline.unlock._commit", lambda *_a, **_k: None):
            outcome = _from_manual_links(
                db,
                row,
                item,
                media,
                NOW,
                [],
                None,
                MagnetDebrid(),
                app.state.settings,
                [],
            )
        assert outcome is not None
        assert "magnet.mkv" in outcome[1]
    finally:
        db.close()