"""Audit fixes: indexes, idle poll queries, the debrid lock, and TMDB 429."""

from datetime import date, datetime, timedelta, timezone

import httpx
from sqlalchemy import event
from tests.conftest import connect_alldebrid_for_tests, csrf, link_create_json, login

from mediatracker.core.pipeline import _settle_obtained_siblings, tick
from mediatracker.core.source_poll import next_due
from mediatracker.db.models import Download, Media, MediaItem
from mediatracker.integrations.tmdb import TmdbClient, TmdbError
from mediatracker.security.logging import redact


def _count(engine):
    queries: list[str] = []

    def before(_conn, _cursor, statement, _parameters, _context, _executemany):
        queries.append(statement)

    event.listen(engine, "before_cursor_execute", before)
    return queries, before


def _seed_followed_media(db, count: int) -> None:
    for index in range(count):
        media = Media(
            type="series" if index % 2 == 0 else "film",
            title=f"Fiche fictive {index:03d}",
            year=2020 + (index % 5),
            tmdb_id=200_000 + index,
            followed=True,
            new_episode_policy="auto",
        )
        db.add(media)
        db.flush()
        if media.type == "film":
            db.add(
                MediaItem(
                    media_id=media.id,
                    season=None,
                    episode=None,
                    title="Film",
                    downloaded=True,
                    watched=index % 2 == 0,
                    watched_at=datetime.now(timezone.utc) if index % 2 == 0 else None,
                    state="downloaded",
                    file_path=f"/fiction/film-{index}.mkv",
                )
            )
        else:
            db.add(
                MediaItem(
                    media_id=media.id,
                    season=1,
                    episode=1,
                    title="Épisode un",
                    air_date=date(2024, 1, 1),
                    downloaded=True,
                    watched=True,
                    watched_at=datetime.now(timezone.utc),
                    state="downloaded",
                    file_path=f"/fiction/serie-{index}-s01e01.mkv",
                )
            )
    db.commit()


def test_list_media_query_budget_is_flat(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        _seed_followed_media(db, 50)
    finally:
        db.close()

    queries_small, hook = _count(app.state.engine)
    try:
        small = client.get("/api/v1/media?limit=10")
    finally:
        event.remove(app.state.engine, "before_cursor_execute", hook)
    assert small.status_code == 200, small.text
    assert len(small.json()["items"]) == 10

    queries_large, hook = _count(app.state.engine)
    try:
        large = client.get("/api/v1/media?limit=50")
    finally:
        event.remove(app.state.engine, "before_cursor_execute", hook)
    assert large.status_code == 200, large.text
    assert len(large.json()["items"]) == 50

    assert len(queries_large) <= 23
    assert abs(len(queries_large) - len(queries_small)) <= 2


def test_webhook_token_is_redacted():
    line = '127.0.0.1 - "GET /api/v1/integrations/jellyfin/webhook?token=super-secret&x=1 HTTP/1.1" 204'
    cleaned = redact(line)
    assert "super-secret" not in cleaned
    assert "token=[redacted]" in cleaned
    assert "x=1" in cleaned


def test_tmdb_429_honors_retry_after():
    calls = {"n": 0}
    slept: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, json={"status_message": "slow"})
        return httpx.Response(200, json={"images": {}})

    client = TmdbClient(
        "token",
        transport=httpx.MockTransport(handler),
        sleeper=slept.append,
    )
    try:
        client.ping()
    finally:
        client.close()
    assert calls["n"] == 2
    assert slept == [2.0]


def test_tmdb_429_gives_up_after_three_attempts():
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(429, headers={"Retry-After": "1"}, json={})

    client = TmdbClient("token", transport=httpx.MockTransport(handler), sleeper=lambda _seconds: None)
    try:
        try:
            client.ping()
        except TmdbError as exc:
            assert exc.code == "RATE_LIMIT"
        else:
            raise AssertionError("expected RATE_LIMIT")
    finally:
        client.close()


def test_next_due_ignores_future_polls(client, app):
    login(client)
    created = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Plus tard", "year": 2020},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    db = app.state.session_factory()
    try:
        media = db.get(Media, created.json()["id"])
        media.next_source_poll_at = datetime.now(timezone.utc) + timedelta(hours=6)
        media.last_source_poll_at = datetime.now(timezone.utc)
        db.commit()
        queries, hook = _count(app.state.engine)
        try:
            assert next_due(db, app.state.settings, datetime.now(timezone.utc)) is None
        finally:
            event.remove(app.state.engine, "before_cursor_execute", hook)
        assert len(queries) <= 5
    finally:
        db.close()


def test_settle_does_not_scan_clean_done_rows(client, app):
    login(client)
    created = client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Fini", "year": 2021},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    db = app.state.session_factory()
    try:
        media = db.get(Media, created.json()["id"])
        item = MediaItem(media_id=media.id, downloaded=True, state="downloaded")
        db.add(item)
        db.flush()
        for _ in range(8):
            db.add(Download(media_item_id=item.id, state="done"))
        db.commit()
        queries, hook = _count(app.state.engine)
        try:
            _settle_obtained_siblings(db, datetime.now(timezone.utc), app.state.settings)
        finally:
            event.remove(app.state.engine, "before_cursor_execute", hook)
        assert len(queries) <= 4
    finally:
        db.close()


def test_unlock_does_not_hold_a_transaction(client, app):
    from mediatracker.providers.base import DirectLink, DownloadStatus

    login(client)
    connect_alldebrid_for_tests(app)
    created = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Verrou", "year": 2022},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    linked = client.post(
        "/api/v1/links",
        json=link_create_json(
            created.json()["id"],
            "https://rapidgator.net/file/lock",
            season=1,
            episode=1,
            start=True,
        ),
        headers=csrf(client),
    )
    assert linked.status_code == 201, linked.text
    seen = {"open": None}
    tick_db = app.state.session_factory()

    class Probe:
        def unlock(self, url: str):
            del url
            seen["open"] = tick_db.in_transaction()
            return DirectLink(url="https://cdn.example/secret.mkv", filename="Episode.mkv", size=32)

    class Downloader:
        def add(self, url, dest_dir, filename, headers=None, extra_options=None):
            del url, headers, extra_options
            dest_dir.mkdir(parents=True, exist_ok=True)
            (dest_dir / (filename or "video.mkv")).write_bytes(b"x" * 32)
            return "g1"

        def status(self, handle):
            del handle
            return DownloadStatus(state="downloading", done_bytes=1, total_bytes=32, speed=1, path=None)

        def remove(self, handle, delete_files=False):
            del handle, delete_files

    try:
        tick(
            tick_db,
            app.state.settings,
            now=datetime(2026, 10, 11, tzinfo=timezone.utc),
            debrid=Probe(),
            downloader=Downloader(),
        )
    finally:
        tick_db.close()
    assert seen["open"] is False


def test_known_files_does_not_hold_a_transaction(client, app):
    from mediatracker.providers.base import DirectLink, DownloadStatus

    login(client)
    connect_alldebrid_for_tests(app)
    created = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Inventaire", "year": 2023},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    linked = client.post(
        "/api/v1/links",
        json=link_create_json(
            created.json()["id"],
            "https://rapidgator.net/file/inventory",
            season=1,
            episode=1,
            start=True,
        ),
        headers=csrf(client),
    )
    assert linked.status_code == 201, linked.text
    seen = {"known": None, "unlock": None}
    tick_db = app.state.session_factory()

    class Probe:
        def known_files(self):
            seen["known"] = tick_db.in_transaction()
            return []

        def unlock(self, url: str):
            del url
            seen["unlock"] = tick_db.in_transaction()
            return DirectLink(url="https://cdn.example/inv.mkv", filename="Episode.mkv", size=32)

    class Downloader:
        def add(self, url, dest_dir, filename, headers=None, extra_options=None):
            del url, headers, extra_options
            dest_dir.mkdir(parents=True, exist_ok=True)
            (dest_dir / (filename or "video.mkv")).write_bytes(b"x" * 32)
            return "g1"

        def status(self, handle):
            del handle
            return DownloadStatus(state="downloading", done_bytes=1, total_bytes=32, speed=1, path=None)

        def remove(self, handle, delete_files=False):
            del handle, delete_files

    try:
        tick(
            tick_db,
            app.state.settings,
            now=datetime(2026, 10, 11, tzinfo=timezone.utc),
            debrid=Probe(),
            downloader=Downloader(),
        )
    finally:
        tick_db.close()
    assert seen["known"] is False
    assert seen["unlock"] is False
