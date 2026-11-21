"""Page source manuelle : profil + replis avant la cascade normale."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import select
from tests.conftest import connect_alldebrid_for_tests, csrf, login, profile_compliant_release_filename
from tests.test_wawacity import BASE
from tests.test_wawacity_hd_label_precheck import InspectingDebrid

from mediatracker.core.pipeline.manual_source_page import pick_manual_source_page_link
from mediatracker.core.pipeline.unlock import _from_manual_source_page
from mediatracker.core.profile_attempt_orchestration import load_profile_chain
from mediatracker.core.profile_fallback import (
    GLOBAL_ALLOW_FALLBACK_KEY,
    PROFILE_FALLBACK_ORDER_KEY,
    format_profile_fallback_order,
    resolve_profiles_for_media,
)
from mediatracker.core.quality_profile import default_profile
from mediatracker.db.models import Download, Media, MediaItem, Provider, QualityProfile, Setting, SourceBinding
from mediatracker.providers.base import LinkCandidate

FIXTURES = Path(__file__).parent / "fixtures" / "wawacity"
PAGE = "/serie/parcours-demo-saison-1-hd"


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
        page_hd_label=True,
        external_id="wawa:rg1",
    )


def test_manual_page_hd_accepted_via_720p_repli_profile(client, db_session, settings):
    login(client)
    from mediatracker.core.quality_profile import ensure_default_profile

    ensure_default_profile(db_session)
    repli = db_session.scalar(select(QualityProfile).where(QualityProfile.name == "720p VF"))
    assert repli is not None
    _upsert_setting(db_session, PROFILE_FALLBACK_ORDER_KEY, format_profile_fallback_order([repli.id]))
    _upsert_setting(db_session, GLOBAL_ALLOW_FALLBACK_KEY, "true")

    media = Media(type="series", title="Parcours Démo", year=2023, allow_fallback=False)
    db_session.add(media)
    db_session.commit()

    chain = load_profile_chain(db_session, media, settings)
    item = MediaItem(season=1, episode=1)
    provider = db_session.scalar(select(Provider).where(Provider.type == "wawacity"))
    pick = pick_manual_source_page_link(
        db_session, provider.id, chain.profiles, [_hd_episode_one()], item, ["rapidgator"]
    )
    assert pick is not None
    ordered, profile, rank = pick
    assert ordered[0].quality == "HD"
    assert profile.name == default_profile().name
    assert rank == 1
    assert default_profile().name == chain.profiles[0].name
    explicit = LinkCandidate(
        url="https://dl-protect.link/explicit",
        kind="ddl_protected",
        hoster="rapidgator",
        quality="1080p",
        language="VFF",
        season=1,
        episode=1,
        filename="Exemple.Fictif.2099.VFF.1080p.WEB.S01E01.mkv",
        external_id="wawa:explicit",
    )
    pick_explicit = pick_manual_source_page_link(
        db_session, provider.id, (chain.profiles[0],), [explicit, _hd_episode_one()], item, ["rapidgator"]
    )
    assert pick_explicit is not None
    assert pick_explicit[0][0].external_id == explicit.external_id


def test_manual_page_no_repli_accepts_provisional_hd(client, db_session, settings):
    login(client)
    from mediatracker.core.quality_profile import ensure_default_profile

    ensure_default_profile(db_session)
    _upsert_setting(db_session, PROFILE_FALLBACK_ORDER_KEY, "")
    _upsert_setting(db_session, GLOBAL_ALLOW_FALLBACK_KEY, "false")

    media = Media(type="series", title="Sans repli", year=2023, allow_fallback=False)
    db_session.add(media)
    db_session.commit()
    profiles = tuple(item.profile for item in resolve_profiles_for_media(db_session, media, settings))
    assert len(profiles) == 1
    item = MediaItem(season=1, episode=1)
    provider = db_session.scalar(select(Provider).where(Provider.type == "wawacity"))
    pick = pick_manual_source_page_link(
        db_session, provider.id, profiles, [_hd_episode_one()], item, ["rapidgator"]
    )
    assert pick is not None
    assert pick[2] == 1


@pytest.fixture
def db_session(client):
    session = client.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


def _wire_manual_page(client, app, db_session, monkeypatch, *, with_repli: bool) -> tuple:
    login(client)
    connect_alldebrid_for_tests(app)
    from mediatracker.core.quality_profile import ensure_default_profile

    ensure_default_profile(db_session)
    if with_repli:
        repli = db_session.scalar(select(QualityProfile).where(QualityProfile.name == "720p VF"))
        _upsert_setting(db_session, PROFILE_FALLBACK_ORDER_KEY, format_profile_fallback_order([repli.id]))
        _upsert_setting(db_session, GLOBAL_ALLOW_FALLBACK_KEY, "true")
    else:
        _upsert_setting(db_session, PROFILE_FALLBACK_ORDER_KEY, "")
        _upsert_setting(db_session, GLOBAL_ALLOW_FALLBACK_KEY, "false")
    client.patch(
        "/api/v1/settings",
        json={"values": {"wawacity_base_url": BASE, "wawacity_user_agent": "AgentFictif/1.0"}},
        headers=csrf(client),
    )

    media = Media(type="series", title="Parcours Démo", year=2023, followed=True, allow_fallback=False)
    db_session.add(media)
    db_session.flush()
    item = MediaItem(media_id=media.id, season=1, episode=1, state="missing")
    db_session.add(item)
    db_session.flush()
    provider = db_session.scalar(select(Provider).where(Provider.type == "wawacity"))
    db_session.add(
        SourceBinding(
            media_id=media.id,
            provider_id=provider.id,
            external_ref=PAGE,
            url=f"{BASE}{PAGE}",
            path=PAGE,
            path_manual=True,
            active=True,
            season=1,
        )
    )
    download = Download(media_item_id=item.id, state="pending", created_at=datetime.now(timezone.utc))
    db_session.add(download)
    db_session.commit()

    html = (FIXTURES / "series_s1_hd_vff.html").read_text(encoding="utf-8")
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text=html)
        if PAGE in str(request.url)
        else httpx.Response(404, text="absent")
    )
    monkeypatch.setattr("mediatracker.providers.sources.loading._shared_pacer", lambda *_a, **_k: None)
    monkeypatch.setattr("mediatracker.providers.sources.loading._shared_cache", lambda *_a, **_k: None)

    def _open_poll(self, db, settings, **kwargs):
        from mediatracker.providers.sources.wawacity import WawacityConfig, WawacitySource

        return WawacitySource(
            WawacityConfig(base_url=BASE),
            transport=kwargs.get("transport") or transport,
        )

    monkeypatch.setattr(
        "mediatracker.providers.sources.wawacity_plugin.WawacitySourceEntry.open_poll",
        _open_poll,
    )
    return media, item, download


def test_from_manual_source_page_unlocks_1080p_on_principal_tour(client, app, db_session, monkeypatch):
    media, item, download = _wire_manual_page(client, app, db_session, monkeypatch, with_repli=True)
    release = profile_compliant_release_filename(season=1, episode=1)
    debrid = InspectingDebrid(filename=release)
    db = app.state.session_factory()
    try:
        media = db.get(Media, media.id)
        item = db.get(MediaItem, item.id)
        row = db.get(Download, download.id)
        journal: list[tuple[str, int]] = []

        def capture_used(*args, **kwargs):
            del args
            journal.append((kwargs.get("profile_name", ""), int(kwargs.get("profile_rank", 0))))
            return None

        with patch("mediatracker.core.pipeline.unlock._commit", lambda *_a, **_k: None):
            with patch(
                "mediatracker.core.pipeline.unlock.record_manual_source_page_used",
                side_effect=capture_used,
            ):
                outcome = _from_manual_source_page(
                    db,
                    row,
                    item,
                    media,
                    datetime.now(timezone.utc),
                    ["rapidgator", "turbobit", "tezfiles", "1fichier"],
                    None,
                    debrid,
                    app.state.settings,
                    [],
                )
        assert outcome is not None
        link, direct_url, *_rest = outcome
        assert journal == [(default_profile().name, 1)]
        assert link.quality == "1080p"
        assert debrid.seen_inspect
        assert debrid.seen_unlock
        assert "video.mkv" in direct_url
    finally:
        db.close()


def test_from_manual_source_page_rejects_720p_on_principal_tour(client, app, db_session, monkeypatch):
    media, item, download = _wire_manual_page(client, app, db_session, monkeypatch, with_repli=True)
    low = "Exemple.Fictif.2099.VFF.720p.WEB.S01E01.mkv"
    debrid = InspectingDebrid(filename=low)
    db = app.state.session_factory()
    try:
        media = db.get(Media, media.id)
        item = db.get(MediaItem, item.id)
        row = db.get(Download, download.id)
        with patch("mediatracker.core.pipeline.unlock._commit", lambda *_a, **_k: None):
            outcome = _from_manual_source_page(
                db,
                row,
                item,
                media,
                datetime.now(timezone.utc),
                ["rapidgator"],
                None,
                debrid,
                app.state.settings,
                [],
            )
        assert outcome is None
        assert debrid.seen_inspect
        assert not debrid.seen_unlock
    finally:
        db.close()


def test_from_manual_source_page_repli_tour_journal_720p(client, app, db_session, monkeypatch):
    """Tour repli : vrai 720p issu de link/infos doit être débridé (sans simuler le pick profil)."""
    media, item, download = _wire_manual_page(client, app, db_session, monkeypatch, with_repli=True)
    repli_row = db_session.scalar(select(QualityProfile).where(QualityProfile.name == "720p VF"))
    low = "Exemple.Fictif.2099.VFF.720p.WEB.S01E01.mkv"
    debrid = InspectingDebrid(filename=low)

    db = app.state.session_factory()
    try:
        media = db.get(Media, media.id)
        item = db.get(MediaItem, item.id)
        row = db.get(Download, download.id)
        row.poll_quality_profile_id = repli_row.id
        db.commit()
        journal: list[tuple[str, int]] = []

        def capture_used(*args, **kwargs):
            del args
            journal.append((kwargs.get("profile_name", ""), int(kwargs.get("profile_rank", 0))))
            return None

        with patch("mediatracker.core.pipeline.unlock._commit", lambda *_a, **_k: None):
            with patch(
                "mediatracker.core.pipeline.unlock.record_manual_source_page_used",
                side_effect=capture_used,
            ):
                outcome = _from_manual_source_page(
                    db,
                    row,
                    item,
                    media,
                    datetime.now(timezone.utc),
                    ["rapidgator", "turbobit", "tezfiles", "1fichier"],
                    None,
                    debrid,
                    app.state.settings,
                    [],
                )
        assert outcome is not None
        assert journal == [("720p VF", 2)]
        assert outcome[0].quality == "HD"
        assert debrid.seen_inspect
        assert debrid.seen_unlock
    finally:
        db.close()


def test_manual_page_journal_uses_tour_profile_when_alldebrid_sends_no_filename(
    client, app, db_session, monkeypatch,
):
    media, item, download = _wire_manual_page(client, app, db_session, monkeypatch, with_repli=True)
    repli_row = db_session.scalar(select(QualityProfile).where(QualityProfile.name == "720p VF"))
    low = "Exemple.Fictif.2099.VFF.720p.WEB.S01E01.mkv"

    class _NoNameDebrid(InspectingDebrid):
        def __init__(self) -> None:
            super().__init__(filename=low)

        def inspect_link(self, url: str):
            self.seen_inspect.append(url)
            from mediatracker.providers.debrid.alldebrid import LinkInspection

            return LinkInspection(
                link=url,
                online=True,
                filename="",
                size=800_000_000,
                code=None,
                message=None,
            )

        def unlock(self, url: str):
            self.seen_unlock.append(url)
            from mediatracker.providers.base import DirectLink

            return DirectLink(url="https://cdn.example/video.mkv", filename="", size=800_000_000)

    debrid = _NoNameDebrid()
    db = app.state.session_factory()
    try:
        media = db.get(Media, media.id)
        item = db.get(MediaItem, item.id)
        row = db.get(Download, download.id)
        row.poll_quality_profile_id = repli_row.id
        db.commit()
        journal: list[tuple[str, int]] = []

        def capture_used(*args, **kwargs):
            del args
            journal.append((kwargs.get("profile_name", ""), int(kwargs.get("profile_rank", 0))))
            return None

        with patch("mediatracker.core.pipeline.unlock._commit", lambda *_a, **_k: None):
            with patch(
                "mediatracker.core.pipeline.unlock.record_manual_source_page_used",
                side_effect=capture_used,
            ):
                outcome = _from_manual_source_page(
                    db,
                    row,
                    item,
                    media,
                    datetime.now(timezone.utc),
                    ["rapidgator"],
                    None,
                    debrid,
                    app.state.settings,
                    [],
                )
        assert outcome is not None
        assert journal == [("720p VF", 2)]
    finally:
        db.close()
