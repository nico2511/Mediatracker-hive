"""Download fallback frozen through the catalog source registry (no network)."""

from datetime import datetime, timezone

import httpx
from tests.conftest import csrf, link_create_json, login
from tests.test_comet import _CacheDebrid, _series_demo, _stub_source
from tests.test_pipeline import FakeDebrid, FakeDownloader, _enable_debrid, _link, _series

from mediatracker.core.pipeline import tick, unlock
from mediatracker.core.pipeline.jobs import _hoster_ok
from mediatracker.core.selection import BLOCKED_SOURCE_HOSTERS, hoster_rank, parse_hoster_preference
from mediatracker.db.models import Link
from mediatracker.providers.sources.registry import get, known_types
from mediatracker.providers.sources.wawacity_plugin import WawacitySourceEntry

NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)


def test_builtin_comet_and_manual_are_registered():
    assert known_types() >= frozenset({"wawacity", "free_telecharger", "comet", "torrentio", "manual"})
    assert get("comet").capabilities == frozenset({"episode"})
    assert get("manual").capabilities == frozenset()
    assert get("wawacity").download_rank == 10
    assert get("free_telecharger").download_rank == 12
    assert get("comet").download_rank == 20
    assert get("torrentio").download_rank == 21


def test_named_source_routes_by_registry_capabilities(monkeypatch):
    seen: list[str] = []

    def hosters(*_args, **_kwargs):
        seen.append("links")
        return "hoster"

    def episode(*_args, **kwargs):
        seen.append(kwargs.get("source_type") or "")
        return "episode"

    monkeypatch.setattr(unlock, "_from_hosters", hosters)
    monkeypatch.setattr(unlock, "_from_episode_source", episode)

    def call(name: str):
        return unlock._from_named_source(
            None,
            name,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            defer_final=False,
        )

    assert call("wawacity") == "hoster"
    assert call("comet") == "episode"
    assert seen == ["links", "comet"]
    assert call("manual") is None
    assert call("no-such-catalog-source") is None


def test_wawacity_hosters_exhausted_defers_to_comet(client, app, monkeypatch):
    login(client)
    _enable_debrid(app)
    media = _series_demo(client, app)
    link = _link(client, media["id"], "https://nitroflare.com/view/ABC", 1)
    source = _stub_source(monkeypatch)
    debrid = _CacheDebrid()
    debrid.mode = "unavailable"
    downloader = FakeDownloader()
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=NOW, debrid=debrid, downloader=downloader)
        from mediatracker.db.models import Download

        row = db.get(Download, link["download_id"])
        assert row.state == "queued"
        assert row.error_code is None
    finally:
        db.close()
    assert source.calls == 1
    assert debrid.uploaded
    assert downloader.urls == ["https://cdn.example/comet.mkv"]


def test_unavailable_hoster_advances_in_the_same_tick(client, app):
    login(client)
    _enable_debrid(app)
    saved = client.patch(
        "/api/v1/settings",
        json={"values": {"hoster_preference": "nitroflare,rapidgator,turbobit,uploady,dailyuploads"}},
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text
    media = _series(client)
    nitro = _link(client, media["id"], "https://nitroflare.com/view/ABC", 4)
    second = client.post(
        "/api/v1/links",
        json=link_create_json(
            media["id"],
            "https://rapidgator.net/file/next",
            season=1,
            episode=4,
            start=False,
        ),
        headers=csrf(client),
    )
    assert second.status_code == 201, second.text
    debrid = FakeDebrid()
    debrid.mode = "nitroflare"
    downloader = FakeDownloader()
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=NOW, debrid=debrid, downloader=downloader)
        from mediatracker.db.models import Download

        row = db.get(Download, nitro["download_id"])
        assert row.state == "queued"
        assert debrid.seen[0].startswith("https://nitroflare.com/")
        assert debrid.seen[1].startswith("https://rapidgator.net/")
    finally:
        db.close()
    assert downloader.urls == ["https://cdn.example/secret.mkv"]


def test_blocked_hosters_are_never_eligible():
    preference = parse_hoster_preference("rapidgator,turbobit,uploady,dailyuploads")
    uptobox = Link(
        id=2,
        media_item_id=1,
        url="https://uptobox.com/file/b",
        kind="ddl_hoster",
        hoster="uptobox",
        dead=False,
    )
    one_fichier = Link(
        id=1,
        media_item_id=1,
        url="https://1fichier.com/a",
        kind="ddl_hoster",
        hoster="1fichier",
        dead=False,
    )
    good = Link(
        id=3,
        media_item_id=1,
        url="https://rapidgator.net/file/ok",
        kind="ddl_hoster",
        hoster="rapidgator",
        dead=False,
    )
    assert not _hoster_ok(uptobox, preference)
    assert _hoster_ok(one_fichier, preference)
    assert _hoster_ok(good, preference)
    assert "uptobox" in BLOCKED_SOURCE_HOSTERS
    assert "1fichier" not in BLOCKED_SOURCE_HOSTERS


def test_1fichier_is_last_in_preference_and_wins_when_alone():
    preference = parse_hoster_preference("rapidgator,turbobit")
    assert preference[-1] == "1fichier"
    assert hoster_rank("rapidgator", preference) < hoster_rank("1fichier", preference)
    only = parse_hoster_preference("1fichier")
    assert only == ["1fichier"]
    normalized = parse_hoster_preference("1fichier,rapidgator,turbobit")
    assert normalized == ["rapidgator", "turbobit", "1fichier"]


def test_settings_normalize_hoster_preference_with_1fichier_last(client):
    login(client)
    saved = client.patch(
        "/api/v1/settings",
        json={"values": {"hoster_preference": "1fichier,rapidgator,turbobit"}},
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text
    rows = {item["key"]: item["value"] for item in saved.json()["settings"]}
    assert rows["hoster_preference"] == "rapidgator,turbobit,1fichier"


def test_uptobox_manual_link_stays_blocked(client, app):
    login(client)
    _enable_debrid(app)
    media = _series(client)
    created = client.post(
        "/api/v1/links",
        json={
            "media_id": media["id"],
            "url": "https://uptobox.com/file/blocked",
            "season": 1,
            "episode": 4,
            "start": True,
        },
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    debrid = FakeDebrid()
    downloader = FakeDownloader()
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=NOW, debrid=debrid, downloader=downloader)
        from mediatracker.db.models import Download

        row = db.get(Download, created.json()["download_id"])
        assert row.state == "failed"
        assert debrid.seen == []
    finally:
        db.close()


def test_wawacity_probe_includes_1fichier_and_omits_uptobox(client, app):
    from pathlib import Path

    login(client)
    html = (Path(__file__).parent / "fixtures" / "wawacity" / "film_hd_vff.html").read_text(encoding="utf-8")
    base = "https://wawa.example"
    saved = client.patch(
        "/api/v1/settings",
        json={"values": {"wawacity_base_url": base}},
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html)

    transport = httpx.MockTransport(handler)
    db = app.state.session_factory()
    settings = app.state.settings
    try:
        entry = WawacitySourceEntry()
        probe = entry.probe_binding_page(db, settings, f"{base}/film/fictive-hd-vff", transport=transport)
    finally:
        db.close()
    assert probe.accessible is True
    assert "1fichier" in probe.links_by_hoster
    assert "uptobox" not in probe.links_by_hoster
    assert probe.links_by_hoster.get("rapidgator", 0) >= 1
