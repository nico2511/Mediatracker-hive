"""Retours recette 26/10/2026 : régressions ciblées."""

from datetime import datetime, timedelta, timezone

import httpx
from tests.conftest import csrf, login

from mediatracker.core.catalog_poster import display_poster_url, tmdb_search_title
from mediatracker.core.source_poster_cache import normalize_source_poster_url
from mediatracker.db.models import CatalogEntry, LibraryFile, Media, MediaItem, Setting
from mediatracker.events import record_event, record_library_imported
from mediatracker.integrations.jellyfin import JellyfinError
from mediatracker.integrations.jellyfin_sync import LAST_POLL_AT, POLL_RETRY_AT, maybe_sync_jellyfin

NOW = datetime(2026, 10, 26, 6, 29, tzinfo=timezone.utc)


class _FailingJellyfin:
    calls = 0

    def refresh_library(self) -> None:
        return None

    def user_items(self, _user_id: str) -> list[dict]:
        type(self).calls += 1
        raise JellyfinError("TRANSPORT", "injoignable")

    def series_index(self, _user_id: str) -> dict:
        return {}

    def users(self) -> list[dict]:
        return [{"Id": "user-1", "Name": "demo"}]

    def close(self) -> None:
        return None


_WEBP_BYTES = b"RIFF\x24\x00\x00\x00WEBPVP8 "


def test_jellyfin_poll_does_not_advance_when_sync_fails(client, app):
    _FailingJellyfin.calls = 0
    login(client)
    saved = client.patch(
        "/api/v1/settings",
        json={
            "values": {
                "jellyfin_url": "http://jellyfin.local:8096",
                "jellyfin_api_key": "jellyfin-test-key",
                "jellyfin_user_id": "user-1",
            }
        },
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text
    db = app.state.session_factory()
    try:
        db.add(Setting(key=LAST_POLL_AT, value=(NOW - timedelta(hours=2)).isoformat(), is_secret=False, updated_at=NOW))
        db.commit()
        maybe_sync_jellyfin(db, app.state.settings, now=NOW, client=_FailingJellyfin())
        row = db.get(Setting, LAST_POLL_AT)
        assert row is not None
        assert datetime.fromisoformat(row.value) < NOW
        retry = db.get(Setting, POLL_RETRY_AT)
        assert retry is not None
        assert datetime.fromisoformat(retry.value) > NOW
        maybe_sync_jellyfin(db, app.state.settings, now=NOW + timedelta(seconds=30), client=_FailingJellyfin())
        assert _FailingJellyfin.calls == 1
    finally:
        db.close()


def test_journal_event_ts_is_timezone_aware(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        record_event(db, "system.updated", when=NOW, payload={"new_version": "0.0.0"})
        db.commit()
    finally:
        db.close()
    page = client.get("/api/v1/events", params={"type": "system.updated", "limit": 1})
    assert page.status_code == 200
    ts = page.json()["items"][0]["ts"]
    assert ts.endswith("+00:00") or ts.endswith("Z")


def test_webhook_stores_nouveautes_and_cleanup_pass(client, app):
    login(client)
    created = client.post(
        "/api/v1/webhooks",
        json={
            "name": "Cible test",
            "type": "generic",
            "url": "https://example.test/hook",
            "secret": "secret-test",
            "events": ["nouveautes", "cleanup.pass", "library.imported"],
        },
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    listed = client.get("/api/v1/webhooks", headers=csrf(client))
    body = listed.json()[-1]
    assert "nouveautes" in body["events"]
    assert "cleanup.pass" in body["events"]
    hook_id = body["id"]
    patched = client.patch(
        f"/api/v1/webhooks/{hook_id}",
        json={"events": ["library.imported", "nouveautes"]},
        headers=csrf(client),
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["events"] == ["library.imported", "nouveautes"]


def test_source_poster_proxy_url_and_relative_normalization(client, app):
    db = app.state.session_factory()
    try:
        base = db.get(Setting, "wawacity_base_url")
        if base is None:
            db.add(Setting(key="wawacity_base_url", value="https://wawa.test", is_secret=False, updated_at=NOW))
        else:
            base.value = "https://wawa.test"
        row = CatalogEntry(
            dedupe_key="rel|2024|film||",
            title="Exemple film",
            title_folded="exemple film",
            year=2024,
            media_type="film",
            source="wawacity",
            source_url="https://wawa.test/film/exemple",
            source_poster_url="/img/films/demo.webp",
            sources=[],
            first_seen_at=NOW,
            last_seen_at=NOW,
        )
        db.add(row)
        db.commit()
        absolute = normalize_source_poster_url(db, app.state.settings, row, row.source_poster_url)
        assert absolute == "https://wawa.test/img/films/demo.webp"
        assert display_poster_url(row) == f"/api/v1/images/source/{row.id}"
    finally:
        db.close()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("demo.webp"):
            return httpx.Response(200, content=_WEBP_BYTES, headers={"content-type": "image/webp"})
        return httpx.Response(404, request=request)

    login(client)
    transport = httpx.MockTransport(handler)
    import mediatracker.core.source_poster_cache as cache_mod

    original = cache_mod.fetch_source_poster

    def _fetch(settings, url, *, allowed_hosts, headers=None):
        with httpx.Client(transport=transport) as http:
            response = http.get(url)
            if response.status_code >= 400:
                raise cache_mod.SourcePosterError("NOT_FOUND", "missing")
            return response.content, "image/webp"

    cache_mod.fetch_source_poster = _fetch
    try:
        response = client.get(f"/api/v1/images/source/{row.id}", headers=csrf(client))
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/")
    finally:
        cache_mod.fetch_source_poster = original


def test_tmdb_search_title_strips_release_noise():
    assert tmdb_search_title("Exemple film [HD] [VFF]") == "Exemple film"
    assert "Saison" not in tmdb_search_title("Exemple série - Saison 2 - VOSTFR")


def test_recent_downloads_use_library_imported_and_group_episodes(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        media = Media(type="series", title="Exemple série", year=2026, followed=True)
        db.add(media)
        db.flush()
        items = []
        for ep in (1, 2, 3):
            item = MediaItem(
                media_id=media.id,
                season=1,
                episode=ep,
                downloaded=True,
                state="downloaded",
                file_path=f"/srv/series/exemple-s01e0{ep}.mkv",
            )
            db.add(item)
            items.append(item)
        db.flush()
        for item in items:
            db.add(
                LibraryFile(
                    path=item.file_path or "",
                    media_type="series",
                    status="matched",
                    media_item_id=item.id,
                    seen_at=NOW,
                )
            )
            record_library_imported(db, media, item, item.file_path or "")
        trashed = MediaItem(
            media_id=media.id,
            season=1,
            episode=9,
            downloaded=True,
            downloaded_at=NOW,
            state="missing",
            file_path="/srv/series/old.mkv",
        )
        db.add(trashed)
        db.commit()
    finally:
        db.close()

    board = client.get("/api/v1/dashboard").json()
    recent = board["recent_downloads"]
    assert len(recent) == 1
    assert recent[0]["media_title"] == "Exemple série"
    assert recent[0]["episode_count"] == 3


def test_jellyfin_webhook_accepts_user_guid_without_dashes(client, app):
    from tests.test_jellyfin import _configure, _episode, _series

    login(client)
    _configure(client)
    media = _series(client)
    _episode(client, media["id"], 1, 2)
    user_guid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    saved = client.patch(
        "/api/v1/settings",
        json={"values": {"jellyfin_user_id": user_guid}},
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text
    done = client.post(
        "/api/v1/integrations/jellyfin/webhook?token=webhook-secret-value",
        json={
            "NotificationType": "PlaybackStop",
            "ItemType": "Episode",
            "ItemId": "jf-1",
            "Provider_tmdb": "900101",
            "SeasonNumber": 1,
            "EpisodeNumber": 2,
            "PlayedToCompletion": True,
            "UserId": "A1B2C3D4E5F67890ABCDEF1234567890",
        },
    )
    assert done.status_code == 200, done.text
    assert done.json()["updated"] == 1
