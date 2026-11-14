"""V5 — validation only: Jellyfin watched sync, TMDB master, refresh after trash and rangement."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from tests.conftest import csrf, login

from mediatracker.db.models import JellyfinLibraryItem, Media, MediaItem, Setting
from mediatracker.integrations.jellyfin import JellyfinClient, JellyfinWatch
from mediatracker.integrations.jellyfin_sync import (
    REFRESH_AT,
    apply_played,
    maybe_sync_jellyfin,
    schedule_library_refresh,
)
from mediatracker.library.jellyfin_align import apply_alignment, build_report

NOW = datetime(2026, 10, 23, 10, tzinfo=timezone.utc)
USER = "11111111-1111-1111-1111-111111111111"

SERIES_TITLE = "Harbor Lantern"
SERIES_YEAR = 2022
SERIES_TMDB = 99001
JELLYFIN_TMDB = 99099


def _configure(client) -> None:
    saved = client.patch(
        "/api/v1/settings",
        json={
            "values": {
                "jellyfin_url": "http://jellyfin.test",
                "jellyfin_api_key": "jellyfin-test-key",
                "jellyfin_user_id": USER,
                "jellyfin_path_prefix": "/srv/media",
                "jellyfin_webhook_secret": "webhook-secret-value",
                "jellyfin_poll_interval_min": 30,
                "jellyfin_push_watched": True,
            }
        },
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text


def _series(client, *, tmdb_id: int | None = SERIES_TMDB) -> dict:
    body: dict = {"type": "series", "title": SERIES_TITLE, "year": SERIES_YEAR}
    if tmdb_id is not None:
        body["tmdb_id"] = tmdb_id
    created = client.post("/api/v1/media", json=body, headers=csrf(client))
    assert created.status_code == 201, created.text
    return created.json()


def _episode(client, media_id: int, season: int, episode: int) -> dict:
    created = client.post(
        f"/api/v1/media/{media_id}/items",
        json={"season": season, "episode": episode, "title": f"E{episode}"},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    return created.json()


def _idle_handler(calls: list):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        path = request.url.path
        if path == "/ScheduledTasks":
            return httpx.Response(200, json=[{"Key": "RefreshLibrary", "State": "Idle"}])
        if path.endswith("/Refresh") or path == "/Library/Refresh":
            return httpx.Response(200, json={})
        if "/PlayedItems/" in path:
            return httpx.Response(204)
        if path.endswith("/Items") and request.method == "GET":
            return httpx.Response(200, json={"Items": [], "TotalRecordCount": 0})
        return httpx.Response(404, json={})

    return handler


def test_v5_jellyfin_pull_updates_watched_both_ways(client, app):
    login(client)
    _configure(client)
    media = _series(client)
    episode = _episode(client, media["id"], 1, 3)
    path = (
        f"/data/media/Series/{SERIES_TITLE} ({SERIES_YEAR})/Season 01/"
        f"{SERIES_TITLE} ({SERIES_YEAR}) - S01E03.mkv"
    )
    db = app.state.session_factory()
    try:
        row = db.get(MediaItem, episode["id"])
        row.file_path = path
        row.downloaded = True
        row.tmdb_episode_id = 7003
        db.commit()
        watch = JellyfinWatch(
            item_id="jf-ep-3",
            item_type="Episode",
            tmdb_id=SERIES_TMDB,
            season=1,
            episode=3,
            path=None,
            played=True,
            user_id=USER,
            series_tmdb_id=SERIES_TMDB,
        )
        assert apply_played(db, row, watch, NOW) is True
        db.commit()
        assert row.watched is True
        assert row.state == "watched"
        assert row.jellyfin_item_id == "jf-ep-3"
        watch = replace(watch, played=False)
        assert apply_played(db, row, watch, NOW + timedelta(minutes=5)) is True
        db.commit()
        assert row.watched is False
        assert row.state == "downloaded"
    finally:
        db.close()


def test_v5_manual_watch_pushes_played_to_jellyfin(client, app, monkeypatch):
    login(client)
    _configure(client)
    media = _series(client)
    episode = _episode(client, media["id"], 1, 1)
    played: list[tuple[str, str]] = []

    class _Client:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def close(self) -> None:
            return None

        def mark_played(self, user_id: str, item_id: str) -> None:
            played.append((user_id, item_id))

    monkeypatch.setattr("mediatracker.integrations.jellyfin.JellyfinClient", _Client)
    monkeypatch.setattr(
        "mediatracker.integrations.jellyfin_sync.resolve_configured_user",
        lambda _db, _client, _configured, _moment=None: USER,
    )
    db = app.state.session_factory()
    try:
        row = db.get(MediaItem, episode["id"])
        row.jellyfin_item_id = "jf-ep-1"
        db.commit()
    finally:
        db.close()
    marked = client.post(
        f"/api/v1/media/{media['id']}/watch",
        json={"season": 1, "until_season": 1, "until_episode": 1},
        headers=csrf(client),
    )
    assert marked.status_code == 200, marked.text
    assert marked.json()["updated"] == 1
    assert played == [(USER, "jf-ep-1")]


def test_v5_jellyfin_tmdb_id_wins_on_alignment(client, app):
    login(client)
    _configure(client)
    media = _series(client, tmdb_id=1)
    db = app.state.session_factory()
    try:
        db.add(
            JellyfinLibraryItem(
                jellyfin_id="series-harbor",
                item_type="Series",
                name=SERIES_TITLE,
                year=SERIES_YEAR,
                series_id="series-harbor",
                tmdb_id=JELLYFIN_TMDB,
                synced_at=NOW,
            )
        )
        row = db.get(Media, media["id"])
        row.jellyfin_series_id = "series-harbor"
        db.commit()
        report = build_report(db, app.state.settings)
        change = report["reidentifies"][0]
        assert change["from_tmdb"] == 1
        assert change["to_tmdb"] == JELLYFIN_TMDB
        calls: list = []
        transport = httpx.MockTransport(_idle_handler(calls))
        jellyfin = JellyfinClient("http://jellyfin.test", "key", USER, transport=transport)
        try:
            apply_alignment(db, app.state.settings, jellyfin, validated=True, now=NOW)
        finally:
            jellyfin.close()
        db.commit()
        row = db.get(Media, media["id"])
        assert row.tmdb_id == JELLYFIN_TMDB
        assert row.title == SERIES_TITLE
        assert row.identity_edited_at is None
    finally:
        db.close()


def test_v5_trash_schedules_jellyfin_refresh(client, app):
    login(client)
    _configure(client)
    created = client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Cedar Post", "year": 2020, "tmdb_id": 99002},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    media_id = created.json()["id"]
    item = client.post(
        f"/api/v1/media/{media_id}/items",
        json={"title": "Cedar Post"},
        headers=csrf(client),
    )
    assert item.status_code == 201, item.text
    data = Path(app.state.settings.data_dir)
    movies = data / "media" / "Films"
    path = movies / "Cedar Post (2020)" / "Cedar Post (2020).mkv"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x" * 40)
    db = app.state.session_factory()
    try:
        row = db.get(MediaItem, item.json()["id"])
        row.file_path = str(path.resolve())
        row.downloaded = True
        row.watched = True
        row.watched_at = NOW
        db.commit()
    finally:
        db.close()
    removed = client.post(
        "/api/v1/cleanup/files",
        json={"media_id": media_id, "scope": "media", "confirm": False},
        headers=csrf(client),
    )
    assert removed.status_code == 200, removed.text
    db = app.state.session_factory()
    try:
        refresh = db.get(Setting, REFRESH_AT)
        assert refresh is not None
        assert refresh.value
    finally:
        db.close()


def test_v5_regroup_apply_schedules_jellyfin_refresh(client, app):
    login(client)
    _configure(client)
    series = Path(app.state.settings.data_dir) / "media" / "Series"
    release = series / "Harbor Lantern FRENCH SAMPLE hoster cove"
    (release / "Season 01").mkdir(parents=True)
    (release / "Season 01" / "Harbor.Lantern.S01E02.mkv").write_bytes(b"v")
    db = app.state.session_factory()
    try:
        db.add(
            Media(
                type="series",
                title=SERIES_TITLE,
                year=SERIES_YEAR,
                tmdb_id=SERIES_TMDB,
                followed=True,
            )
        )
        db.commit()
    finally:
        db.close()
    listed = client.get("/api/v1/library/regroup")
    group = next(row for row in listed.json()["groups"] if row["title"] == SERIES_TITLE)
    applied = client.post(
        "/api/v1/library/regroup",
        json={"media_ids": [group["media_id"]], "validated": True},
        headers=csrf(client),
    )
    assert applied.status_code == 200, applied.text
    db = app.state.session_factory()
    try:
        assert db.get(Setting, REFRESH_AT) is not None
    finally:
        db.close()


def test_v5_debounced_refresh_fires_once_after_rangement_burst(client, app):
    login(client)
    _configure(client)

    class _Jellyfin:
        def __init__(self) -> None:
            self.refreshed = 0

        def refresh_library(self) -> None:
            self.refreshed += 1

        def user_items(self, _user_id: str) -> list[dict]:
            return []

        def close(self) -> None:
            return None

    fake = _Jellyfin()
    db = app.state.session_factory()
    try:
        schedule_library_refresh(db, NOW)
        schedule_library_refresh(db, NOW + timedelta(seconds=15))
        db.commit()
        maybe_sync_jellyfin(db, app.state.settings, now=NOW + timedelta(seconds=30), client=fake)
        assert fake.refreshed == 0
        maybe_sync_jellyfin(db, app.state.settings, now=NOW + timedelta(seconds=61), client=fake)
        assert fake.refreshed == 1
    finally:
        db.close()
