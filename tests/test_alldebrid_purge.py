"""AllDebrid purge stays on the official delete routes and does not fan out per poll."""

import logging
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs

import httpx
from tests.conftest import connect_alldebrid_for_tests, csrf, link_create_json, login

from mediatracker.core.debrid_purge import PurgePacer, maybe_auto_purge, purge_account, summary
from mediatracker.core.pipeline import tick
from mediatracker.db.models import Download, Media, MediaItem
from mediatracker.providers.base import DirectLink, DownloadStatus
from mediatracker.providers.debrid.alldebrid import AllDebridClient, KnownFile


def _client(handler):
    return AllDebridClient("test-key", transport=httpx.MockTransport(handler), sleeper=lambda _s: None)


def _ok(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"status": "success", "data": {"message": "ok", "magnets": [{"id": 7}]}})


def test_delete_routes_match_the_official_api():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok(request)

    client = _client(handler)
    client.delete_saved_links(["https://rapidgator.net/file/abc", "https://turbobit.net/file/other"])
    client.delete_history(["https://rapidgator.net/file/abc"])
    client.delete_magnet("15")
    client.delete_history(None)
    client.list_magnet_ids()

    saved, history, magnet, wipe, listing = seen
    assert saved.method == "POST"
    assert saved.url.path == "/v4/user/links/delete"
    assert parse_qs(saved.content.decode())["links[]"] == [
        "https://rapidgator.net/file/abc",
        "https://turbobit.net/file/other",
    ]
    assert history.url.path == "/v4/user/history/delete"
    assert parse_qs(history.content.decode())["links[]"] == ["https://rapidgator.net/file/abc"]
    assert magnet.url.path == "/v4/magnet/delete"
    assert parse_qs(magnet.content.decode())["id"] == ["15"]
    assert wipe.url.path == "/v4/user/history/delete"
    assert wipe.content == b""
    assert listing.method == "GET"
    assert listing.url.path == "/v4/magnet/status"
    assert client.list_magnet_ids() == ["7"]


def test_purge_batches_links_and_spaces_calls(caplog):
    calls: list[tuple] = []

    class Client:
        def delete_saved_links(self, links):
            calls.append(("saved", tuple(links)))

        def delete_history(self, links=None):
            calls.append(("history", None if links is None else tuple(links)))

        def delete_magnet(self, magnet_id):
            calls.append(("magnet", magnet_id))

    clock = {"now": 0.0}
    slept: list[float] = []

    def sleeper(seconds: float) -> None:
        slept.append(seconds)
        clock["now"] += seconds

    pacer = PurgePacer(1.0, clock=lambda: clock["now"], sleeper=sleeper)
    ours = [f"https://rapidgator.net/file/{index}" for index in range(21)]
    with caplog.at_level(logging.INFO, logger="mediatracker.core.debrid_purge"):
        report = purge_account(
            Client(),
            saved=ours,
            history=ours[:2],
            magnets=["9"],
            pacer=pacer,
        )
    assert [name for name, _payload in calls] == ["saved", "saved", "history", "magnet"]
    assert len(calls[0][1]) == 20
    assert len(calls[1][1]) == 1
    assert calls[2][1] == tuple(ours[:2])
    assert slept == [1.0, 1.0, 1.0]
    assert report.saved == 21
    assert report.history == 2
    assert report.magnets == 1
    assert report.history_wiped is False
    assert len(caplog.records) == 1
    assert "rapidgator" not in caplog.records[0].message
    assert caplog.records[0].message == summary(report)


def test_full_history_wipe_is_a_single_empty_post():
    calls: list[tuple] = []

    class Client:
        def delete_saved_links(self, links):
            calls.append(("saved", tuple(links)))

        def delete_history(self, links=None):
            calls.append(("history", links))

        def delete_magnet(self, magnet_id):
            calls.append(("magnet", magnet_id))

    report = purge_account(
        Client(),
        saved=["https://rapidgator.net/file/abc"],
        history=None,
        magnets=["3"],
        wipe_history=True,
        pacer=PurgePacer(0),
    )
    assert calls == [
        ("saved", ("https://rapidgator.net/file/abc",)),
        ("history", None),
        ("magnet", "3"),
    ]
    assert report.history_wiped is True
    assert "historique récent vidé" in summary(report)


def _finished(db, *, link: str | None, magnet: str | None = None, purged: bool = False) -> Download:
    media = Media(type="film", title="Archipel Pilote", year=2021, created_at=datetime.now(timezone.utc))
    db.add(media)
    db.flush()
    item = MediaItem(media_id=media.id, state="downloaded", downloaded=True)
    db.add(item)
    db.flush()
    row = Download(
        media_item_id=item.id,
        state="done",
        debrid_link=link,
        debrid_magnet_id=magnet,
        debrid_purged_at=datetime.now(timezone.utc) if purged else None,
        created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.commit()
    return row


class _Account:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.saved: list[KnownFile] = []
        self.magnets: list[str] = []

    def saved_links(self):
        self.calls.append(("list-saved",))
        return list(self.saved)

    def list_magnet_ids(self):
        self.calls.append(("list-magnets",))
        return list(self.magnets)

    def delete_saved_links(self, links):
        self.calls.append(("saved", tuple(links)))

    def delete_history(self, links=None):
        self.calls.append(("history", None if links is None else tuple(links)))

    def delete_magnet(self, magnet_id):
        self.calls.append(("magnet", magnet_id))


def test_auto_purge_runs_once_for_a_filed_download(client, app):
    del client
    account = _Account()
    now = datetime(2026, 10, 11, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        row = _finished(db, link="https://rapidgator.net/file/abc", magnet="15")
        first = maybe_auto_purge(db, app.state.settings, account, now, pacer=PurgePacer(0))
        assert first is not None and first.error is None
        assert ("saved", ("https://rapidgator.net/file/abc",)) in account.calls
        assert ("history", ("https://rapidgator.net/file/abc",)) in account.calls
        assert ("magnet", "15") in account.calls
        db.flush()
        db.refresh(row)
        assert row.debrid_purged_at is not None
        account.calls.clear()
        assert maybe_auto_purge(db, app.state.settings, account, now, pacer=PurgePacer(0)) is None
        assert account.calls == []
    finally:
        db.close()


def test_filed_download_purges_the_hoster_link_not_the_cdn(client, app, monkeypatch):
    login(client)
    connect_alldebrid_for_tests(app)
    created = client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Archipel Pilote", "year": 2021},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    linked = client.post(
        "/api/v1/links",
        json=link_create_json(
            created.json()["id"],
            "https://rapidgator.net/file/abc",
            season=None,
            episode=None,
            start=True,
        ),
        headers=csrf(client),
    )
    assert linked.status_code == 201, linked.text

    class Debrid(_Account):
        def unlock(self, url: str) -> DirectLink:
            self.calls.append(("unlock", url))
            return DirectLink(url="https://cdn.example/secret.mkv", filename="Archipel Pilote.mkv", size=32)

        def resolve(self, url: str):
            return [url]

    debrid = Debrid()
    debrid.saved = [KnownFile("https://rapidgator.net/file/abc", "Archipel Pilote.mkv", 32, "rapidgator", "saved")]

    class Downloader:
        def __init__(self) -> None:
            self._status = {}

        def add(self, url: str, dest_dir: Path, filename: str | None, headers=None, extra_options=None) -> str:
            del headers, extra_options
            dest_dir.mkdir(parents=True, exist_ok=True)
            path = dest_dir / (filename or "video.mkv")
            path.write_bytes(b"x" * 32)
            self._status["g1"] = DownloadStatus(
                state="downloading",
                done_bytes=8,
                total_bytes=32,
                speed=4,
                path=str(path),
            )
            return "g1"

        def status(self, handle: str) -> DownloadStatus:
            del handle
            return self._status["g1"]

        def complete(self) -> None:
            current = self._status["g1"]
            self._status["g1"] = current.model_copy(update={"state": "complete", "done_bytes": 32, "speed": None})

    monkeypatch.setattr("mediatracker.core.debrid_purge.PurgePacer", lambda *args, **kwargs: PurgePacer(0))
    downloader = Downloader()
    now = datetime(2026, 10, 11, tzinfo=timezone.utc)
    stored = app.state.session_factory()
    try:
        tick(stored, app.state.settings, now=now, debrid=debrid, downloader=downloader)
        job = stored.get(Download, linked.json()["download_id"])
        assert job.debrid_link == "https://rapidgator.net/file/abc"
        assert "cdn.example" not in (job.debrid_link or "")
        downloader.complete()
        tick(stored, app.state.settings, now=now, debrid=debrid, downloader=downloader)
        stored.refresh(job)
        assert job.state == "done"
        assert job.debrid_purged_at is not None
    finally:
        stored.close()
    assert ("saved", ("https://rapidgator.net/file/abc",)) in debrid.calls
    assert ("history", ("https://rapidgator.net/file/abc",)) in debrid.calls
    saved = [call for call in debrid.calls if call[0] == "saved"]
    assert not any("cdn.example" in "".join(call[1]) for call in saved)


def test_auto_purge_respects_the_setting(client, app):
    login(client)
    saved = client.patch(
        "/api/v1/settings",
        json={"values": {"alldebrid_auto_purge": False}},
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text
    account = _Account()
    db = app.state.session_factory()
    try:
        _finished(db, link="https://rapidgator.net/file/abc")
        assert (
            maybe_auto_purge(
                db,
                app.state.settings,
                account,
                datetime.now(timezone.utc),
                pacer=PurgePacer(0),
            )
            is None
        )
        assert account.calls == []
    finally:
        db.close()


def test_manual_purge_twice_keeps_pooled_httpx_client(client, app, monkeypatch):
    """Two API purges in a row must not close the shared outbound httpx pool."""
    from mediatracker.core.outbound_http import close_outbound_http_clients
    from mediatracker.providers.debrid.alldebrid import AllDebridClient

    close_outbound_http_clients()
    login(client)
    shared = AllDebridClient("test-key")
    peer = AllDebridClient("other-key")
    assert shared._client is peer._client
    assert not shared._owns_client

    account = _Account()
    account.saved = [KnownFile("https://rapidgator.net/file/abc", "a.mkv", 1, "rapidgator", "saved")]

    class Provider:
        client = shared

        def saved_links(self):
            return account.saved_links()

        def list_magnet_ids(self):
            return account.list_magnet_ids()

        def delete_saved_links(self, links):
            return account.delete_saved_links(links)

        def delete_history(self, links=None):
            return account.delete_history(links)

        def delete_magnet(self, magnet_id):
            return account.delete_magnet(magnet_id)

    provider = Provider()
    db = app.state.session_factory()
    try:
        _finished(db, link="https://rapidgator.net/file/abc", magnet="15")
    finally:
        db.close()

    monkeypatch.setattr("mediatracker.api.v1.alldebrid._debrid", lambda *_args, **_kwargs: (None, provider))
    monkeypatch.setattr("mediatracker.api.v1.alldebrid.PurgePacer", lambda *args, **kwargs: PurgePacer(0))

    for _ in range(2):
        response = client.post(
            "/api/v1/integrations/alldebrid/purge",
            json={"scope": "mediatracker"},
            headers=csrf(client),
        )
        assert response.status_code == 200, response.text

    assert not shared._client.is_closed
    assert not peer._client.is_closed
    close_outbound_http_clients()


def test_maybe_auto_purge_does_not_hold_a_transaction(client, app):
    del client
    purge_seen = {"open": None}
    tick_db = app.state.session_factory()
    now = datetime(2026, 10, 11, tzinfo=timezone.utc)

    class PurgeProbe(_Account):
        def delete_saved_links(self, links):
            purge_seen["open"] = tick_db.in_transaction()
            return super().delete_saved_links(links)

    try:
        _finished(tick_db, link="https://rapidgator.net/file/xyz", magnet="16")
        maybe_auto_purge(tick_db, app.state.settings, PurgeProbe(), now, pacer=PurgePacer(0))
    finally:
        tick_db.close()
    assert purge_seen["open"] is False


def test_manual_purge_keeps_links_media_tracker_did_not_create(client, app, monkeypatch):
    login(client)
    account = _Account()
    account.saved = [
        KnownFile("https://rapidgator.net/file/abc", "a.mkv", 1, "rapidgator", "saved"),
        KnownFile("https://turbobit.net/file/foreign", "other.mkv", 1, "turbobit", "saved"),
    ]
    db = app.state.session_factory()
    try:
        _finished(db, link="https://rapidgator.net/file/abc", magnet="15")
    finally:
        db.close()

    monkeypatch.setattr("mediatracker.api.v1.alldebrid._debrid", lambda *_args, **_kwargs: (None, account))
    monkeypatch.setattr("mediatracker.api.v1.alldebrid.PurgePacer", lambda *args, **kwargs: PurgePacer(0))

    refused = client.post(
        "/api/v1/integrations/alldebrid/purge",
        json={"scope": "all", "confirm": False},
        headers=csrf(client),
    )
    assert refused.status_code == 422

    selective = client.post(
        "/api/v1/integrations/alldebrid/purge",
        json={"scope": "mediatracker"},
        headers=csrf(client),
    )
    assert selective.status_code == 200, selective.text
    body = selective.json()
    assert body["saved"] == 1
    assert body["history"] == 1
    assert body["magnets"] == 1
    assert body["history_wiped"] is False
    assert "turbobit" not in body["message"]
    saved_calls = [item[1] for item in account.calls if item[0] == "saved"]
    assert saved_calls == [("https://rapidgator.net/file/abc",)]
    history_calls = [item[1] for item in account.calls if item[0] == "history"]
    assert history_calls == [("https://rapidgator.net/file/abc",)]
    assert ("list-magnets",) not in account.calls

    account.calls.clear()
    account.saved = [
        KnownFile("https://rapidgator.net/file/abc", "a.mkv", 1, "rapidgator", "saved"),
        KnownFile("https://turbobit.net/file/foreign", "other.mkv", 1, "turbobit", "saved"),
    ]
    account.magnets = ["15", "99"]
    everything = client.post(
        "/api/v1/integrations/alldebrid/purge",
        json={"scope": "all", "confirm": True},
        headers=csrf(client),
    )
    assert everything.status_code == 200, everything.text
    assert everything.json()["history_wiped"] is True
    assert ("history", None) in account.calls
    assert ("saved", ("https://rapidgator.net/file/abc", "https://turbobit.net/file/foreign")) in account.calls
    assert ("magnet", "99") in account.calls
