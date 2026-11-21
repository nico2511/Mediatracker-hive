"""F7 — REDISCOVER quand les liens sont morts, cooldown recherche distante par fiche."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
from tests.conftest import csrf, login

from mediatracker.core.forget import TRACE_FILE_ABSENT, forget_local_file
from mediatracker.core.pipeline import tick
from mediatracker.core.pipeline.constants import REDISCOVER, REDISCOVER_MESSAGE, REDISCOVER_RESERVED
from mediatracker.core.pipeline.jobs import _ingest_other_versions
from mediatracker.core.precheck import NO_LINK_RETRY_LATER, REDOWNLOAD_STARTED
from mediatracker.core.remote_search_limit import REMOTE_SEARCH_COOLDOWN
from mediatracker.db.models import Download, Link, Media, MediaItem, Provider, Setting
from mediatracker.providers.sources.wawacity import SourcePacer

NOW = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)
BASE = "https://catalogue-fictif.example"


def _enable_wawacity(client) -> None:
    saved = client.patch(
        "/api/v1/settings",
        json={"values": {"wawacity_base_url": BASE, "wawacity_user_agent": "AgentFictif/1.0"}},
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text


def _series(client, title: str = "Brume Azur") -> tuple[int, int]:
    created = client.post(
        "/api/v1/media",
        json={"type": "series", "title": title, "year": 2026, "tmdb_id": 77001},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    media_id = created.json()["id"]
    episode = client.post(
        f"/api/v1/media/{media_id}/items",
        json={"season": 1, "episode": 2, "title": "Quai", "air_date": "2026-01-08"},
        headers=csrf(client),
    )
    assert episode.status_code == 201, episode.text
    return media_id, episode.json()["id"]


def _dead_link(
    db,
    item_id: int,
    url: str = "https://rapidgator.net/file/brume-azur",
    *,
    external_id: str = "brume-rg-dead",
) -> Link:
    provider = db.query(Provider).filter(Provider.type == "wawacity").one()
    link = Link(
        media_item_id=item_id,
        provider_id=provider.id,
        kind="ddl_protected",
        url=url,
        hoster="rapidgator",
        external_id=external_id,
        priority=10,
        dead=True,
        last_error="Lien hors ligne.",
    )
    db.add(link)
    return link


def _mark_absent(db, media_id: int, item_id: int) -> None:
    media = db.get(Media, media_id)
    item = db.get(MediaItem, item_id)
    item.downloaded = True
    item.file_path = "/tmp/ghost/Brume.Azur.S01E02.mkv"
    forget_local_file(db, media, item, NOW)
    assert any(t.get("kind") == TRACE_FILE_ABSENT for t in (item.traces or []))


def _counting_transport(gets: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "GET" and url.startswith(BASE):
            gets.append(url)
        return httpx.Response(404, text="absent")

    return httpx.MockTransport(handler)


def _patch_httpx(monkeypatch, gets: list[str]) -> None:
    monkeypatch.setattr(SourcePacer, "wait", lambda self: 0.0)
    real_client = httpx.Client

    def factory(*args, **kwargs):
        kwargs = {key: value for key, value in kwargs.items() if key != "transport"}
        kwargs["transport"] = _counting_transport(gets)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)


def test_rediscover_throttle_counts_gets(client, app, monkeypatch):
    from tests.conftest import connect_alldebrid_for_tests

    login(client)
    connect_alldebrid_for_tests(app)
    _enable_wawacity(client)
    media_id, item_id = _series(client)
    db = app.state.session_factory()
    try:
        link = _dead_link(db, item_id)
        _mark_absent(db, media_id, item_id)
        db.commit()
        link_id = link.id
    finally:
        db.close()

    gets: list[str] = []
    _patch_httpx(monkeypatch, gets)

    first = client.post(f"/api/v1/items/{item_id}/redownload", headers=csrf(client))
    assert first.status_code == 200, first.text
    assert first.json()["message"] == REDOWNLOAD_STARTED
    download_id = first.json()["download_id"]

    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=NOW)
        row = db.get(Download, download_id)
        assert row.error_code != REDISCOVER
        dead = db.get(Link, link_id)
        assert dead.dead is True
        assert gets, "une recherche attendue au premier passage"
    finally:
        db.close()

    assert len(gets) == 1

    second = client.post(f"/api/v1/items/{item_id}/redownload", headers=csrf(client))
    assert second.status_code == 200, second.text
    assert second.json()["message"] == NO_LINK_RETRY_LATER
    assert second.json()["status"] == "no_link"
    assert len(gets) == 1

    later = NOW + REMOTE_SEARCH_COOLDOWN + timedelta(minutes=1)
    db = app.state.session_factory()
    try:
        media = db.get(Media, media_id)
        media.last_remote_search_at = NOW - REMOTE_SEARCH_COOLDOWN - timedelta(minutes=1)
        db.commit()
    finally:
        db.close()
    third = client.post(f"/api/v1/items/{item_id}/redownload", headers=csrf(client))
    assert third.status_code == 200, third.text
    assert third.json()["message"] == REDOWNLOAD_STARTED
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=later)
    finally:
        db.close()
    assert len(gets) == 2


def test_schedule_full_retries_one_remote_search_per_fiche(client, app, monkeypatch):
    login(client)
    _enable_wawacity(client)
    media_id, item1 = _series(client, title="Double Ligne")
    episode = client.post(
        f"/api/v1/media/{media_id}/items",
        json={"season": 1, "episode": 3, "title": "Suite", "air_date": "2026-01-15"},
        headers=csrf(client),
    )
    assert episode.status_code == 201, episode.text
    item2 = episode.json()["id"]

    gets: list[str] = []
    _patch_httpx(monkeypatch, gets)

    db = app.state.session_factory()
    try:
        _dead_link(db, item1, external_id="brume-rg-dead-ep2")
        _dead_link(
            db,
            item2,
            url="https://rapidgator.net/file/brume-azur-ep3",
            external_id="brume-rg-dead-ep3",
        )
        db.flush()
        _mark_absent(db, media_id, item1)
        media = db.get(Media, media_id)
        other = db.get(MediaItem, item2)
        other.downloaded = True
        other.file_path = "/tmp/ghost/Brume.Azur.S01E03.mkv"
        forget_local_file(db, media, other, NOW)
        first = Download(media_item_id=item1, state="failed", error_code="NO_LINK", error="échec")
        second = Download(media_item_id=item2, state="failed", error_code="NO_LINK", error="échec")
        db.add_all([first, second])
        db.commit()
        assert first.error_code == "NO_LINK"
        from mediatracker.core.pipeline.queue import schedule_full_retries

        scheduled = schedule_full_retries(db, [first, second], NOW, app.state.settings)
        assert scheduled == 2
        assert first.error_code == REDISCOVER
        assert second.error_code == REDISCOVER
        assert first.error == REDISCOVER_MESSAGE
        assert second.error == REDISCOVER_MESSAGE
        assert "Wawacity" not in first.error
        db.commit()
        for step in range(6):
            tick(db, app.state.settings, now=NOW + timedelta(seconds=step * 6))
    finally:
        db.close()

    assert len(gets) <= 1


def test_dead_links_are_not_revived_on_redownload(client, app):
    login(client)
    media_id, item_id = _series(client, title="Rivage Ouest")
    db = app.state.session_factory()
    try:
        link = _dead_link(db, item_id, url="https://turbobit.net/file/rivage-ouest")
        _mark_absent(db, media_id, item_id)
        db.commit()
        link_id = link.id
    finally:
        db.close()

    with patch("mediatracker.core.source_poll.run_poll"):
        response = client.post(f"/api/v1/items/{item_id}/redownload", headers=csrf(client))
    assert response.status_code == 200, response.text
    db = app.state.session_factory()
    try:
        row = db.get(Download, response.json()["download_id"])
        assert row.error_code == REDISCOVER_RESERVED
        assert db.get(Link, link_id).dead is True
    finally:
        db.close()


def test_ingest_other_versions_shares_the_remote_search_limit(client, app):
    from tests.conftest import connect_alldebrid_for_tests

    del client
    connect_alldebrid_for_tests(app)
    db = app.state.session_factory()
    try:
        media = Media(type="series", title="Sable Nord", year=2026, pref_quality="HD", pref_language="VFF")
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, season=1, episode=1, title="Pilot")
        db.add(item)
        setting = db.get(Setting, "wawacity_base_url")
        if setting is None:
            db.add(Setting(key="wawacity_base_url", value=BASE))
        else:
            setting.value = BASE
        provider = db.query(Provider).filter(Provider.type == "wawacity").one()
        provider.enabled = True
        media.last_remote_search_at = NOW
        db.commit()

        searches = {"count": 0}

        def _forbid_search(*args, **kwargs):
            searches["count"] += 1
            return []

        with patch("mediatracker.core.source_poll._search", _forbid_search):
            assert _ingest_other_versions(db, app.state.settings, media, item, NOW) is False
        assert searches["count"] == 0

        media.last_remote_search_at = NOW - REMOTE_SEARCH_COOLDOWN - timedelta(seconds=1)
        with patch("mediatracker.core.source_poll._search", _forbid_search):
            _ingest_other_versions(db, app.state.settings, media, item, NOW)
        assert searches["count"] == 1
    finally:
        db.close()
