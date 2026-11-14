"""Hotfix: Comet backoff, redirector aliases, aria2 502, link checks, queue pause."""

import base64
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from tests.conftest import csrf, link_create_json, login
from tests.test_comet import _candidates, _DiscontinuedDebrid, _patch_candidates, _series_demo
from tests.test_pipeline import FakeDebrid, FakeDownloader, _enable_debrid

from mediatracker.core.pipeline import (
    _direct_reject_reason,
    _magnet_keys,
    _redirector_targets,
    tick,
)
from mediatracker.core.selection import parse_hoster_preference
from mediatracker.core.source_poll import expected_slots
from mediatracker.db.models import Download, Link, Media, MediaItem
from mediatracker.providers.base import DownloadStatus, EpisodeQuery, SourcePreferences
from mediatracker.providers.debrid.alldebrid import AllDebridClient, AllDebridConfig, AllDebridError, AllDebridProvider
from mediatracker.providers.sources.comet import select_streams
from mediatracker.providers.sources.manual import hoster_of
from mediatracker.providers.sources.wawacity_html import parse_heading, parse_links, release_name_from_row


def test_release_name_is_parsed_from_the_link_row():
    raw = (
        "Lien 1: Practical.Magic.2.2026.MULTi.VF2.1080p.WEBRip.EAC3.5.1.x264-GL0PNESS.mkv | Rapidgator | 6.6 Go"
    )
    assert release_name_from_row(raw) == (
        "Practical.Magic.2.2026.MULTi.VF2.1080p.WEBRip.EAC3.5.1.x264-GL0PNESS.mkv"
    )
    html = """
    <html><body>
      <ul><li>Langue <b>MULTI</b></li><li>Qualité <b>1080p</b></li></ul>
      <table>
        <tr>
          <td>Lien 1: Practical.Magic.2.2026.MULTi.VF2.1080p.WEBRip.EAC3.5.1.x264-GL0PNESS.mkv</td>
          <td>Rapidgator</td>
          <td>6.6 Go</td>
          <td><a href="https://dl-protect.link/abc?fn=Practical">Télécharger</a></td>
        </tr>
      </table>
    </body></html>
    """
    _heading, links = parse_links(html, "https://www.wawacity.fitness/?p=film&id=60429")
    assert len(links) == 1
    assert links[0].hoster == "rapidgator"
    assert links[0].filename.endswith("-GL0PNESS.mkv")
    assert links[0].quality == "1080p"
    assert links[0].language == "MULTI"


def test_link_quality_from_page_meta_field_only():
    html = """
    <html><body>
      <ul><li>Qualité <b>1080p</b></li></ul>
      <table>
        <tr>
          <td>Lien 1: Fictif.Demo.2099.MULTi.VF2.1080p.WEB-DL.x264.mkv</td>
          <td>Rapidgator</td>
          <td>2 Go</td>
          <td><a href="https://dl-protect.link/abc?fn=fictif">Télécharger</a></td>
        </tr>
      </table>
    </body></html>
    """
    _heading, links = parse_links(html, "https://www.wawacity.fitness/?p=film&id=1")
    assert len(links) == 1
    assert links[0].quality == "1080p"


def test_link_quality_from_release_with_scene_group_suffix():
    html = """
    <html><body>
      <table>
        <tr>
          <td>Lien 1: Exemple.Fictif.2099.MULTi.1080p.WEB-GRP.mkv</td>
          <td>Rapidgator</td>
          <td>3 Go</td>
          <td><a href="https://dl-protect.link/abc?fn=exemple">Télécharger</a></td>
        </tr>
      </table>
    </body></html>
    """
    _heading, links = parse_links(html, "https://www.wawacity.fitness/?p=film&id=2")
    assert len(links) == 1
    assert links[0].quality == "1080p"


def test_link_quality_720p_stays_below_1080p_tier():
    html = """
    <html><body>
      <ul><li>Qualité <b>720p</b></li></ul>
      <table>
        <tr>
          <td>Lien 1: Fictif.Demo.2099.MULTi.720p.WEB.x264-GROUP.mkv</td>
          <td>Rapidgator</td>
          <td>1.2 Go</td>
          <td><a href="https://dl-protect.link/abc?fn=fictif720">Télécharger</a></td>
        </tr>
      </table>
    </body></html>
    """
    _heading, links = parse_links(html, "https://www.wawacity.fitness/?p=film&id=3")
    assert len(links) == 1
    assert links[0].quality == "HD"


def test_heading_quality_ignores_marketing_4k_when_bracket_tag_is_720p():
    heading = parse_heading("Exemple Fictif 4K [HD 720p] - MULTI")
    assert heading.quality == "HD"


def test_heading_quality_ignores_1080_in_title_when_bracket_tag_is_720p():
    heading = parse_heading("Exemple Fictif 1080 [HD 720p] - MULTI")
    assert heading.quality == "HD"


def test_hoster_aliases_cover_short_domains():
    assert hoster_of("https://rg.to/file/abc") == "rapidgator"
    assert hoster_of("https://rapidgator.asia/file/abc") == "rapidgator"
    assert hoster_of("https://trbt.cc/folder/xyz") == "turbobit"
    assert hoster_of("https://turbobit.cc/folder/xyz") == "turbobit"
    assert hoster_of("https://hitf.to/abc") == "turbobit"


def test_redirector_keeps_unknown_hosts_and_virtual_links():
    preference = parse_hoster_preference("rapidgator,turbobit,uploady")
    targets = _redirector_targets(
        [
            "https://redirect.alldebrid.com/dl/token",
            "https://trbt.cc/abc",
            "https://example-host.net/file",
            "https://1fichier.com/nope",
        ],
        preference,
    )
    assert "https://1fichier.com/nope" in targets
    assert "https://trbt.cc/abc" in targets
    assert "https://example-host.net/file" in targets
    assert targets[-1] == "https://redirect.alldebrid.com/dl/token"


def test_redirector_logs_the_alldebrid_error_without_secrets(caplog):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "error",
                "error": {
                    "code": "REDIRECTOR_ERROR",
                    "message": "Could not extract https://dl-protect.link/abc?fn=secret api_key=supersecret",
                },
            },
        )

    client = AllDebridClient("test-key", transport=httpx.MockTransport(handler), sleeper=lambda _s: None)
    with caplog.at_level(logging.INFO, logger="mediatracker.providers.debrid.alldebrid"):
        try:
            client.redirector("https://dl-protect.link/abc?fn=secretname")
        except AllDebridError as exc:
            assert exc.code == "REDIRECTOR_ERROR"
        else:
            raise AssertionError("erreur attendue")
    text = caplog.text
    assert "REDIRECTOR_ERROR" in text
    assert "supersecret" not in text
    assert "test-key" not in text
    assert "fn=" not in text
    assert "secretname" not in text
    assert "dl-protect.link" in text


def test_missing_host_alias_still_asks_alldebrid():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "success", "data": {"hosts": {"rg.to": {"status": True}}}})

    provider = AllDebridProvider(AllDebridConfig(api_key="test-key"))
    provider.client = AllDebridClient("test-key", transport=httpx.MockTransport(handler), sleeper=lambda _s: None)
    from mediatracker.providers.base import LinkCandidate

    assert provider.supports(
        LinkCandidate(url="https://trbt.cc/x", kind="ddl_hoster", hoster="turbobit", external_id="t")
    )


def test_exact_release_outranks_a_larger_different_group():
    name = "Practical.Magic.2.2026.MULTi.VF2.1080p.WEBRip.EAC3.5.1.x264-GL0PNESS.mkv"
    query = EpisodeQuery(
        title="Practical Magic 2",
        year=2026,
        media_type="film",
        imdb_id="tt9",
        release_names=[name],
    )
    other = "Practical.Magic.2.2026.MULTi.1080p.WEB.x264-OTHER.mkv"
    exact = "a" * 40
    bigger = "b" * 40
    payload = {
        "streams": [
            {"infoHash": bigger, "title": other, "behaviorHints": {"filename": other}, "size": 9_000_000_000},
            {"infoHash": exact, "title": name, "behaviorHints": {"filename": name}, "size": 1000},
        ]
    }
    chosen = select_streams(payload, query, SourcePreferences(quality="1080p", language="MULTI"))
    assert chosen[0].external_id.startswith(f"comet:{exact}")


def test_btih_hex_and_base32_are_the_same_magnet():
    raw = bytes.fromhex("ab" * 20)
    base32 = base64.b32encode(raw).decode().rstrip("=").lower()
    hexed = raw.hex()
    assert _magnet_keys(f"magnet:?xt=urn:btih:{base32}", None) == {hexed}
    assert _magnet_keys(f"magnet:?xt=urn:btih:{hexed}", f"comet:{hexed}:0") == {hexed}


def test_html_and_tiny_unlocked_links_are_rejected():
    def html(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html>erreur</html>")

    reason = _direct_reject_reason(
        "https://debrid.it/f/x",
        6_000_000_000,
        transport=httpx.MockTransport(html),
    )
    assert reason and "vidéo" in reason

    def tiny(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "video/x-matroska", "content-length": "2600"},
            content=b"\x1a\x45\xdf\xa3" + b"x" * 20,
        )

    reason = _direct_reject_reason(
        "https://debrid.it/f/y",
        6_000_000_000,
        transport=httpx.MockTransport(tiny),
    )
    assert reason and "vidéo" in reason

    def video(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            206,
            headers={"content-type": "video/x-matroska", "content-range": "bytes 0-63/7000000000"},
            content=b"\x1a\x45\xdf\xa3" + b"x" * 20,
        )

    assert _direct_reject_reason("https://debrid.it/f/z", 7_000_000_000, transport=httpx.MockTransport(video)) is None


def test_bad_direct_link_tries_the_next_hoster(client, app, monkeypatch):
    login(client)
    _enable_debrid(app)
    media = client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Boîte Démo", "year": 2026},
        headers=csrf(client),
    )
    assert media.status_code == 201, media.text
    first = client.post(
        "/api/v1/links",
        json=link_create_json(
            media.json()["id"],
            "https://rapidgator.net/file/html",
            season=None,
            episode=None,
            start=True,
        ),
        headers=csrf(client),
    )
    assert first.status_code == 201, first.text
    second = client.post(
        "/api/v1/links",
        json=link_create_json(
            media.json()["id"],
            "https://turbobit.net/file/ok",
            season=None,
            episode=None,
            start=False,
        ),
        headers=csrf(client),
    )
    assert second.status_code == 201, second.text
    db = app.state.session_factory()
    try:
        for link in db.query(Link).all():
            link.filename = "Boîte Démo.2026.MULTi.1080p.WEBRip.x264-GROUP.mkv"
        db.commit()
    finally:
        db.close()

    def reject(url, expected, **kwargs):
        del expected, kwargs
        if url and "secret" in url and not getattr(reject, "used", False):
            reject.used = True
            return "Le lien débridé n'est pas une vidéo (page d'erreur ou fichier trop petit)."
        return None

    monkeypatch.setattr("mediatracker.core.pipeline._direct_reject_reason", reject)
    debrid = FakeDebrid()
    now = datetime(2026, 10, 12, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=now, debrid=debrid, downloader=FakeDownloader())
        row = db.get(Download, first.json()["download_id"])
        assert row.state == "queued"
        buried = db.query(Link).filter(Link.url.contains("rapidgator")).one()
        assert buried.dead is True
        listed = client.get("/api/v1/downloads").json()["items"][0]
        assert "Boîte Démo.2026" in (listed["release"] or "")
        assert listed["hoster"] == "turbobit"
    finally:
        db.close()


class _CdnDownloader(FakeDownloader):
    def status(self, handle: str) -> DownloadStatus:
        current = super().status(handle)
        return current.model_copy(
            update={"state": "error", "error": "29: Download aborted"}
        )


def test_aria2_502_retries_then_reunlocks_then_next_hoster(client, app, monkeypatch):
    login(client)
    _enable_debrid(app)
    monkeypatch.setattr("mediatracker.core.pipeline._direct_reject_reason", lambda *args, **kwargs: None)
    media = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Exemple", "year": 2024},
        headers=csrf(client),
    )
    assert media.status_code == 201, media.text
    first = client.post(
        "/api/v1/links",
        json=link_create_json(
            media.json()["id"],
            "https://rapidgator.net/file/cdn",
            season=1,
            episode=1,
            start=True,
        ),
        headers=csrf(client),
    )
    assert first.status_code == 201, first.text
    second = client.post(
        "/api/v1/links",
        json=link_create_json(
            media.json()["id"],
            "https://uploady.io/file/next",
            season=1,
            episode=1,
            start=False,
        ),
        headers=csrf(client),
    )
    assert second.status_code == 201, second.text
    debrid = FakeDebrid()
    downloader = _CdnDownloader()
    moment = datetime(2026, 10, 12, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        buried = None
        row = None
        for _ in range(12):
            tick(db, app.state.settings, now=moment, debrid=debrid, downloader=downloader)
            row = db.get(Download, first.json()["download_id"])
            buried = db.query(Link).filter(Link.url.contains("rapidgator")).one()
            if buried.dead and row.state == "queued" and row.link_id != buried.id:
                break
            due = row.next_retry_at
            if due is not None and due.tzinfo is None:
                due = due.replace(tzinfo=timezone.utc)
            if due is not None and due > moment:
                moment = due + timedelta(seconds=1)
        assert buried is not None and buried.dead is True
        assert row is not None and row.state == "queued"
        assert row.link_id != buried.id
        assert len(debrid.seen) >= 2
    finally:
        db.close()


def test_completed_job_clears_sibling_errors(client, app):
    login(client)
    media = client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Sorcières Démo 2", "year": 2026},
        headers=csrf(client),
    )
    assert media.status_code == 201, media.text
    created = client.post(
        "/api/v1/links",
        json={"media_id": media.json()["id"], "url": "https://rapidgator.net/file/old", "start": True},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    now = datetime(2026, 10, 12, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        failed = db.get(Download, created.json()["download_id"])
        failed.state = "failed"
        failed.error_code = "REDIRECTOR_ERROR"
        failed.error = "Le redirecteur n'a pas pu extraire les liens."
        item = db.get(MediaItem, failed.media_item_id)
        folder = Path(app.state.settings.data_dir) / "media" / "Films" / "Sorcières Démo 2 (2026)"
        folder.mkdir(parents=True)
        video = folder / "Sorcières Démo 2 (2026).mkv"
        video.write_bytes(b"film")
        item.downloaded = True
        item.file_path = str(video)
        item.state = "downloaded"
        winner = Download(media_item_id=item.id, state="done", progress=100, created_at=now, finished_at=now)
        db.add(winner)
        db.commit()
        tick(db, app.state.settings, now=now, debrid=FakeDebrid(), downloader=FakeDownloader())
        db.refresh(failed)
        assert failed.state == "done"
        assert failed.dismissed is True
        assert failed.error_code is None
    finally:
        db.close()
    listed = client.get("/api/v1/downloads").json()["items"]
    assert all(row["error_code"] != "REDIRECTOR_ERROR" for row in listed)


def test_queue_pause_and_resume(client, app):
    login(client)
    paused = client.post("/api/v1/downloads/pause", headers=csrf(client))
    assert paused.status_code == 200, paused.text
    body = paused.json()
    assert body["queue_held"] is True
    assert body["queue_paused"] is True
    listed = client.get("/api/v1/downloads")
    assert listed.json()["queue_held"] is True
    resumed = client.post("/api/v1/downloads/resume", headers=csrf(client))
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["queue_held"] is False


def test_downloaded_film_is_not_polled_again(client, app):
    login(client)
    created = client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Obtenu", "year": 2024, "new_episode_policy": "auto"},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    media_id = created.json()["id"]
    db = app.state.session_factory()
    try:
        media = db.get(Media, media_id)
        media.followed = True
        media.next_air_date = datetime(2026, 10, 11, tzinfo=timezone.utc).date()
        media.next_source_poll_at = datetime(2026, 10, 12, tzinfo=timezone.utc)
        item = MediaItem(
            media_id=media.id,
            title="Obtenu",
            downloaded=True,
            file_path="/data/Films/Obtenu.mkv",
            state="downloaded",
        )
        db.add(item)
        db.commit()
        slots = expected_slots(
            media,
            [item],
            today=datetime(2026, 10, 13, tzinfo=timezone.utc).date(),
            window_days=30,
            force=True,
            catch_up=True,
        )
        assert slots == []
    finally:
        db.close()
    fiche = client.get(f"/api/v1/media/{media_id}")
    assert fiche.status_code == 200, fiche.text
    body = fiche.json()
    assert body["poll"]["reason"] == "film_obtained"
    assert body["poll"]["allowed"] is False
    assert body["auto_status"] == "Obtenu"


def test_discontinued_magnets_pause_after_three_uploads(client, app, monkeypatch):
    login(client)
    _enable_debrid(app)
    media = _series_demo(client, app, order="comet")
    link = client.post(
        "/api/v1/links",
        json=link_create_json(
            media["id"],
            "https://rapidgator.net/file/abc",
            season=1,
            episode=1,
            start=True,
        ),
        headers=csrf(client),
    )
    assert link.status_code == 201, link.text
    idents = [f"{index:040x}" for index in range(1, 5)]
    _patch_candidates(monkeypatch, _candidates(*idents))
    debrid = _DiscontinuedDebrid()
    uploaded: list[str] = []

    def upload(magnet: str):
        uploaded.append(magnet)
        raise AllDebridError("DISCONTINUED", "This magnet is discontinued", retryable=False)

    debrid.upload_magnet = upload
    now = datetime(2026, 10, 12, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        with caplog_cm() as messages:
            tick(db, app.state.settings, now=now, debrid=debrid, downloader=FakeDownloader())
        row = db.get(Download, link.json()["download_id"])
        assert row.state == "pending"
        assert row.next_retry_at is not None
        due = row.next_retry_at
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        assert due > now
        assert len(uploaded) == 3
        assert any("job=" in line and "hash=" in line and "episode=" in line for line in messages)
    finally:
        db.close()


@contextmanager
def caplog_cm():
    logger = logging.getLogger("mediatracker.core.pipeline")
    found: list[str] = []

    class _Handler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            found.append(record.getMessage())

    handler = _Handler()
    logger.addHandler(handler)
    try:
        yield found
    finally:
        logger.removeHandler(handler)


def test_direct_reject_fails_open_when_the_cdn_is_unreachable():
    assert _direct_reject_reason("https://cdn.example/secret.mkv", 32) is None
