"""Wawacity parsing, pacing, and the poll that hands links to the download queue.

Fixtures stand in for the live site. No test opens a socket.
"""

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from tests.conftest import csrf, login

from mediatracker.core.pipeline import tick
from mediatracker.core.source_poll import run_poll
from mediatracker.core.source_select import select_variants
from mediatracker.db.models import Download, Link, Media, MediaItem, Provider
from mediatracker.providers.base import DirectLink, DownloadStatus, Health, SourceResult
from mediatracker.providers.sources.throttle import HtmlCache, SourcePacer, backoff_delay
from mediatracker.providers.sources.wawacity import WawacityConfig, WawacitySource
from mediatracker.providers.sources.wawacity_html import parse_links, parse_search

FIXTURES = Path(__file__).parent / "fixtures" / "wawacity"
NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
BASE = "https://wawa.test"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


class _Debrid:
    def __init__(self) -> None:
        self.resolved: list[str] = []
        self.unlocked: list[str] = []

    def health(self) -> Health:
        return Health(status="ok", code=None, message="ok", checked_at=NOW)

    def resolve(self, url: str) -> list[str]:
        self.resolved.append(url)
        return ["https://rapidgator.net/file/from-redirector"]

    def unlock(self, url: str) -> DirectLink:
        self.unlocked.append(url)
        return DirectLink(url="https://cdn.example/secret.mkv", filename="Hidden.Release.WEB.mkv", size=32)

    def link_infos(self, links: list[str]):
        return []

    def saved_links(self):
        return []

    def history(self):
        return []

    def magnet_cache(self, magnets: list[str]):
        return []


class _Downloader:
    def __init__(self) -> None:
        self.urls: list[str] = []
        self._status: dict[str, DownloadStatus] = {}

    def add(self, url: str, dest_dir: Path, filename: str | None, headers=None, extra_options=None) -> str:
        del headers, extra_options
        self.urls.append(url)
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / (filename or "video.mkv")
        path.write_bytes(b"x" * 32)
        gid = f"g{len(self._status) + 1}"
        self._status[gid] = DownloadStatus(state="downloading", done_bytes=8, total_bytes=32, speed=4, path=str(path))
        return gid

    def status(self, handle: str) -> DownloadStatus:
        return self._status[handle]

    def remove(self, handle: str, delete_files: bool = False) -> None:
        del handle, delete_files

    def complete(self) -> None:
        for gid, current in list(self._status.items()):
            self._status[gid] = current.model_copy(update={"state": "complete", "done_bytes": 32, "speed": None})


def _source(handler, *, cache: HtmlCache | None = None, pacer: SourcePacer | None = None) -> WawacitySource:
    return WawacitySource(
        WawacityConfig(base_url=BASE, user_agent="TestAgent/1.0"),
        transport=httpx.MockTransport(handler),
        cache=cache,
        pacer=pacer or SourcePacer(0, sleeper=lambda _seconds: None),
    )


def _pages(calls: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        assert request.headers["user-agent"] == "TestAgent/1.0"
        url = str(request.url)
        if "p=series" in url:
            return httpx.Response(200, text=_text("search_series.html"))
        if "p=films" in url:
            return httpx.Response(200, text=_text("search_film.html"))
        if "agent-nuit-demo-saison-1-1080p-multi" in url:
            return httpx.Response(200, text=_text("series_s1_1080p_multi.html"))
        if (
            "agent-nuit-demo-saison-1" in url
            and "vfq" not in url
            and "saison-2" not in url
        ):
            return httpx.Response(200, text=_text("series_s1_hd_vff.html"))
        if "archipel-pilote-2021" in url:
            return httpx.Response(200, text=_text("film_hd_vff.html"))
        if "saison-1-hd-vff" in url:
            return httpx.Response(200, text=_text("series_s1_hd_vff.html"))
        if "archipel-pilote-2021-hd-vff" in url:
            return httpx.Response(200, text=_text("film_hd_vff.html"))
        return httpx.Response(404, text="absent")

    return handler


def test_parse_search_matches_title_year_and_season():
    hits = parse_search(_text("search_series.html"), BASE)
    titles = {(hit.title, hit.year, hit.season, hit.quality, hit.language) for hit in hits}
    assert ("Agent Nuit Démo", 2023, 1, "HD", "VFF") in titles
    assert ("Agent Nuit Démo", 2023, 2, "HD", "VFF") in titles
    assert ("Autre Série", 2019, 1, "HD", "VFF") in titles
    assert all(hit.media_type == "series" or hit.title.startswith("Agent Nuit") for hit in hits)


def test_quality_language_and_hoster_order():
    hits = parse_search(_text("search_series.html"), BASE)
    results = [
        SourceResult(
            title=hit.title,
            year=hit.year,
            media_type=hit.media_type,
            ref=hit.url,
            url=hit.url,
            season=hit.season,
            quality=hit.quality,
            language=hit.language,
        )
        for hit in hits
    ]
    strict = select_variants(
        results,
        title="Agent Nuit Démo",
        year=2023,
        media_type="series",
        seasons={1},
        quality="HD",
        language="VFF",
        allow_fallback=False,
        quality_fallback=("HD", "1080p", "HDLight"),
        language_fallback=("VFF", "MULTI", "VF"),
    )
    assert [page.quality for page in strict] == ["HD"]
    assert [page.language for page in strict] == ["VFF"]
    assert "1080p" not in strict[0].url

    without_preferred = [page for page in results if not (page.quality == "HD" and page.language == "VFF")]
    fallback = select_variants(
        without_preferred,
        title="Agent Nuit Démo",
        year=2023,
        media_type="series",
        seasons={1},
        quality="HD",
        language="VFF",
        allow_fallback=True,
        quality_fallback=("HD", "1080p", "HDLight"),
        language_fallback=("VFF", "MULTI", "VF"),
    )
    assert fallback[0].language == "VFF"
    assert fallback[0].quality == "HDLight"

    _heading, links = parse_links(_text("series_s1_hd_vff.html"), f"{BASE}/serie/agent-nuit-demo-saison-1-hd-vff")
    episode_one = [link for link in links if link.episode == 1]
    assert [link.hoster for link in episode_one] == [
        "tezfiles",
        "1fichier",
        "rapidgator",
        "uptobox",
        "turbobit",
        "nitroflare",
        "katfile",
    ]


def test_get_links_orders_hosters_and_keeps_1fichier_last():
    html = _text("series_s1_hd_vff.html")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html)

    source = _source(handler, cache=HtmlCache(0))
    from mediatracker.providers.base import SourceRef

    links = [link for link in source.get_links(SourceRef(ref=f"{BASE}/page", url=f"{BASE}/page")) if link.episode == 1]
    source.close()
    assert [link.hoster for link in links] == [
        "rapidgator",
        "turbobit",
        "nitroflare",
        "katfile",
        "tezfiles",
        "1fichier",
    ]
    assert all(link.kind == "ddl_protected" for link in links)
    assert all("uptobox" not in link.url for link in links)


def test_pacer_waits_and_backoff_doubles():
    clock = _Clock()
    pacer = SourcePacer(5, clock=clock, sleeper=clock.sleep, rng=random.Random(0))
    assert pacer.wait() == 0
    second = pacer.wait()
    assert 5 <= second <= 6
    assert backoff_delay(1, 180) == timedelta(minutes=180)
    assert backoff_delay(2, 180) == timedelta(minutes=360)
    assert backoff_delay(3, 180) == timedelta(minutes=720)
    assert backoff_delay(6, 180) == timedelta(minutes=1440)


def test_cache_skips_the_second_fetch():
    calls: list[str] = []
    source = _source(_pages(calls), cache=HtmlCache(900))
    source.search("Agent Nuit Démo", "series")
    source.search("Agent Nuit Démo", "series")
    source.close()
    assert len(calls) == 1


def test_cloudflare_and_429_back_off(client, app, caplog):
    login(client)
    media = _series(client, policy="auto")
    _episodes(client, media["id"])

    def challenge(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(403, headers={"cf-ray": "abc", "server": "cloudflare"}, text="Just a moment")

    report = _poll(app, media["id"], challenge)
    assert report.result == "error"
    assert "Cloudflare" in report.message
    assert "dl-protect" not in report.message
    assert "dl-protect" not in caplog.text
    row = _media(app, media["id"])
    assert row.source_error_streak == 1
    assert row.next_source_poll_at is not None
    delta = _utc(row.next_source_poll_at) - NOW
    assert timedelta(minutes=180) <= delta <= timedelta(minutes=198)

    def limited(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(429, text="slow down")

    again = _poll(app, media["id"], limited)
    assert again.result == "error"
    row = _media(app, media["id"])
    assert row.source_error_streak == 2
    delta = _utc(row.next_source_poll_at) - NOW
    assert timedelta(minutes=360) <= delta <= timedelta(minutes=396)


def test_propose_lists_episodes_and_auto_queues(client, app):
    login(client)
    proposed = _series(client, policy="propose")
    _episodes(client, proposed["id"])
    calls: list[str] = []
    report = _poll(app, proposed["id"], _pages(calls))
    assert report.result == "found"
    assert report.queued == 0
    assert report.proposed == 2
    assert not any("saison-2" in url or "vfq" in url for url in calls)
    db = app.state.session_factory()
    try:
        items = list(db.query(MediaItem).filter(MediaItem.media_id == proposed["id"]).all())
        by_episode = {item.episode: item for item in items}
        assert by_episode[1].source_available is True
        assert by_episode[1].downloaded is False
        assert by_episode[3].source_available is False
        links = list(db.query(Link).filter(Link.media_item_id == by_episode[1].id).all())
        assert [link.hoster for link in sorted(links, key=lambda row: -row.priority)] == [
            "rapidgator",
            "turbobit",
            "1fichier",
        ]
        assert all(link.kind == "ddl_protected" for link in links)
        assert db.query(Download).count() == 0
        body = db.get(Media, proposed["id"])
        assert body.last_poll_result == "found"
        assert body.last_source_poll_at is not None
        assert body.next_source_poll_at is not None
    finally:
        db.close()

    board = client.get("/api/v1/dashboard").json()
    offered = [card for card in board["available"] if card["media_id"] == proposed["id"]]
    assert {card["episode"] for card in offered} == {1, 2}
    started = client.post(
        "/api/v1/downloads",
        json={"media_item_id": offered[0]["item_id"]},
        headers=csrf(client),
    )
    assert started.status_code == 201, started.text
    assert started.json()["state"] == "pending"
    episodes = client.get(f"/api/v1/media/{proposed['id']}/items").json()["seasons"][0]["episodes"]
    offered_item = next(row for row in episodes if row["id"] == offered[0]["item_id"])
    assert offered_item["download_active"] is True

    automatic = _series(client, policy="auto")
    _episodes(client, automatic["id"])
    queued = _poll(app, automatic["id"], _pages([]))
    assert queued.queued == 2
    assert queued.proposed == 0
    listed = client.get("/api/v1/downloads").json()
    assert sum(1 for row in listed["items"] if row["media_id"] == automatic["id"]) == 2


def test_gate_skips_future_and_finished_series(client, app):
    login(client)
    future = _series(client, policy="auto")
    client.post(
        f"/api/v1/media/{future['id']}/items",
        json={"season": 1, "episode": 1, "title": "Plus tard", "air_date": "2026-12-01"},
        headers=csrf(client),
    )
    calls: list[str] = []
    assert _due_poll(app, future["id"], _pages(calls)) is None
    assert calls == []

    ended = client.patch(
        f"/api/v1/media/{future['id']}",
        json={"series_status": "ended", "next_air_date": "2026-10-11"},
        headers=csrf(client),
    )
    assert ended.status_code == 200
    calls.clear()
    assert _due_poll(app, future["id"], _pages(calls)) is None
    assert calls == []


def test_manual_check_ignores_the_interval(client, app, monkeypatch):
    login(client)
    _enable_debrid(app)
    client.patch(
        "/api/v1/settings",
        json={"values": {"wawacity_base_url": BASE, "wawacity_user_agent": "TestAgent/1.0"}},
        headers=csrf(client),
    )
    media = _series(client, policy="propose")
    _episodes(client, media["id"])
    calls: list[str] = []
    _poll(app, media["id"], _pages(calls))
    assert calls
    monkeypatch.setattr(SourcePacer, "wait", lambda self: 0.0)
    real_client = httpx.Client

    def factory(*args, **kwargs):
        kwargs = {key: value for key, value in kwargs.items() if key != "transport"}
        kwargs["transport"] = httpx.MockTransport(_pages(calls))
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)
    calls.clear()
    checked = client.post(f"/api/v1/media/{media['id']}/check", headers=csrf(client))
    assert checked.status_code == 200, checked.text
    assert checked.json()["result"] == "found"
    fiche = client.get(f"/api/v1/media/{media['id']}").json()
    assert fiche["poll"]["last_result"] == "found"
    assert fiche["poll"]["last_check_at"]
    assert fiche["poll"]["next_check_at"]


def test_auto_download_uses_redirector_and_tmdb_filename(client, app):
    login(client)
    _enable_debrid(app)
    media = _series(client, policy="auto")
    _episodes(client, media["id"])
    _poll(app, media["id"], _pages([]))
    debrid = _Debrid()
    downloader = _Downloader()
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=NOW, debrid=debrid, downloader=downloader)
        assert debrid.resolved
        assert all("dl-protect.link" in url for url in debrid.resolved)
        assert debrid.unlocked
        assert set(debrid.unlocked) == {"https://rapidgator.net/file/from-redirector"}
        downloader.complete()
        tick(db, app.state.settings, now=NOW, debrid=debrid, downloader=downloader)
        stored = json.dumps(
            {
                "links": [row.url for row in db.query(Link).all()],
                "files": [row.filename for row in db.query(Download).all()],
            },
            default=str,
        )
        assert "cdn.example" not in stored
        assert "Hidden.Release" not in stored
        dest = (
            Path(app.state.settings.data_dir)
            / "media"
            / "Series"
            / "Agent Nuit Démo (2023)"
            / "Season 01"
            / "Agent Nuit Démo (2023) - S01E01.mkv"
        )
        assert dest.is_file()
    finally:
        db.close()
    raw = Path(app.state.settings.database_url.removeprefix("sqlite:///")).read_bytes()
    assert b"cdn.example" not in raw


def test_film_year_match_and_library_name(client, app):
    login(client)
    _enable_debrid(app)
    created = client.post(
        "/api/v1/media",
        json={
            "type": "film",
            "title": "Archipel Pilote",
            "year": 2021,
            "new_episode_policy": "auto",
            "next_air_date": "2026-10-10",
        },
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    calls: list[str] = []
    report = _poll(app, created.json()["id"], _pages(calls))
    assert report.queued == 1
    assert any("archipel-pilote-2021" in url for url in calls)
    assert not any("1984" in url or "part-two" in url for url in calls)
    debrid = _Debrid()
    downloader = _Downloader()
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=NOW, debrid=debrid, downloader=downloader)
        downloader.complete()
        tick(db, app.state.settings, now=NOW, debrid=debrid, downloader=downloader)
        films = Path(app.state.settings.data_dir) / "media" / "Films" / "Archipel Pilote (2021)"
        dest = films / "Archipel Pilote (2021).mkv"
        assert dest.is_file()
    finally:
        db.close()


def _series(client, *, policy: str, title: str = "Agent Nuit Démo") -> dict:
    created = client.post(
        "/api/v1/media",
        json={
            "type": "series",
            "title": title,
            "year": 2023,
            "new_episode_policy": policy,
            "series_status": "returning",
            "pref_quality": "HD",
            "pref_language": "VFF",
        },
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    return created.json()


def _episodes(client, media_id: int) -> None:
    for number, air in ((1, "2026-10-11"), (2, "2026-10-11"), (3, "2026-12-01")):
        created = client.post(
            f"/api/v1/media/{media_id}/items",
            json={"season": 1, "episode": number, "title": f"E{number}", "air_date": air},
            headers=csrf(client),
        )
        assert created.status_code == 201, created.text


def _poll(app, media_id: int, handler):
    db = app.state.session_factory()
    try:
        media = db.get(Media, media_id)
        provider = db.query(Provider).filter(Provider.type == "wawacity").one()
        source = _source(handler, cache=HtmlCache(0))
        try:
            return run_poll(
                db,
                media,
                provider.id,
                source,
                app.state.settings,
                now=NOW,
                rng=random.Random(1),
                force=True,
            )
        finally:
            source.close()
    finally:
        db.close()


def _due_poll(app, media_id: int, handler):
    from mediatracker.providers.sources.loading import poll_if_due

    db = app.state.session_factory()
    try:
        source = _source(handler, cache=HtmlCache(0))
        provider = db.query(Provider).filter(Provider.type == "wawacity").one()
        try:
            return poll_if_due(
                db,
                app.state.settings,
                now=NOW,
                rng=random.Random(1),
                sources=[(provider.id, source)],
            )
        finally:
            source.close()
    finally:
        db.close()


def _media(app, media_id: int) -> Media:
    db = app.state.session_factory()
    try:
        row = db.get(Media, media_id)
        db.expunge(row)
        return row
    finally:
        db.close()


def _enable_debrid(app) -> None:
    from tests.conftest import connect_alldebrid_for_tests

    connect_alldebrid_for_tests(app)
