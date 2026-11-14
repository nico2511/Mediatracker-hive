"""Jellyfin mapping, webhook, polling fallback, and debounced library refresh."""

import json
from datetime import datetime, timedelta, timezone

import httpx
from tests.conftest import csrf, login

from mediatracker.db.models import Media, MediaItem
from mediatracker.integrations.jellyfin import JellyfinClient, JellyfinError, parse_webhook, paths_match
from mediatracker.integrations.jellyfin_sync import (
    apply_watches,
    match_media_items,
    maybe_sync_jellyfin,
    schedule_library_refresh,
)
from mediatracker.integrations.tmdb import TmdbClient


class _Jellyfin:
    def __init__(self, items: list[dict] | None = None) -> None:
        self.items = items or []
        self.refreshed = 0
        self.closed = False

    def refresh_library(self) -> None:
        self.refreshed += 1

    def user_items(self, user_id: str) -> list[dict]:
        assert user_id == "user-1"
        return self.items

    def users(self) -> list[dict]:
        return [{"Id": "user-1", "Name": "nico"}]

    def close(self) -> None:
        self.closed = True


def _series(client) -> dict:
    created = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Exemple", "year": 2024, "tmdb_id": 900101},
        headers=csrf(client),
    )
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


def _configure(client) -> None:
    saved = client.patch(
        "/api/v1/settings",
        json={
            "values": {
                "jellyfin_url": "http://jellyfin.local:8096",
                "jellyfin_api_key": "jellyfin-test-key",
                "jellyfin_user_id": "user-1",
                "jellyfin_path_prefix": "/srv/media",
                "jellyfin_webhook_secret": "webhook-secret-value",
                "jellyfin_poll_interval_min": 30,
            }
        },
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text


def test_paths_and_provider_ids_map_to_the_episode(client, app):
    login(client)
    media = _series(client)
    episode = _episode(client, media["id"], 1, 2)
    special = _episode(client, media["id"], 0, 1)
    film = client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Archipel Pilote", "year": 2021, "tmdb_id": 900005},
        headers=csrf(client),
    )
    assert film.status_code == 201, film.text
    film_item = client.post(
        f"/api/v1/media/{film.json()['id']}/items",
        json={"title": "Archipel Pilote"},
        headers=csrf(client),
    )
    assert film_item.status_code == 201, film_item.text
    db = app.state.session_factory()
    try:
        row = db.get(MediaItem, episode["id"])
        row.file_path = "/data/media/Series/Exemple (2024)/Season 01/Exemple (2024) - S01E02.mkv"
        row.tmdb_episode_id = 555
        special_row = db.get(MediaItem, special["id"])
        special_row.file_path = "/data/media/Series/Exemple (2024)/Season 00/Exemple (2024) - S00E01.mkv"
        db.commit()
        by_slot = match_media_items(
            db,
            parse_webhook(
                {
                    "NotificationType": "UserDataSaved",
                    "ItemType": "Episode",
                    "ItemId": "slot",
                    "Provider_tmdb": "900101",
                    "SeasonNumber": 1,
                    "EpisodeNumber": 2,
                    "Played": True,
                    "UserId": "user-1",
                }
            ),
            None,
        )
        assert [item.id for item in by_slot] == [episode["id"]]
        by_episode_id = match_media_items(
            db,
            parse_webhook(
                {
                    "NotificationType": "UserDataSaved",
                    "ItemType": "Episode",
                    "ItemId": "ep",
                    "Provider_tmdb": "555",
                    "Played": True,
                }
            ),
            None,
        )
        assert [item.id for item in by_episode_id] == [episode["id"]]
        remote = "/srv/media/Series/Exemple (2024)/Season 01/Exemple (2024) - S01E02.mkv"
        assert paths_match(row.file_path, remote, "/srv/media")
        by_path = match_media_items(
            db,
            parse_webhook(
                {
                    "NotificationType": "UserDataSaved",
                    "ItemType": "Episode",
                    "ItemId": "path",
                    "Path": remote,
                    "Played": True,
                }
            ),
            "/srv/media",
        )
        assert [item.id for item in by_path] == [episode["id"]]
        by_special = match_media_items(
            db,
            parse_webhook(
                {
                    "NotificationType": "UserDataSaved",
                    "ItemType": "Episode",
                    "ItemId": "special",
                    "Provider_tmdb": "900101",
                    "ParentIndexNumber": 0,
                    "IndexNumber": 1,
                    "Played": False,
                }
            ),
            None,
        )
        assert [item.id for item in by_special] == [special["id"]]
        movie = parse_webhook(
            {
                "NotificationType": "UserDataSaved",
                "ItemType": "Movie",
                "ItemId": "movie",
                "Provider_tmdb": 900005,
                "Played": True,
            }
        )
        assert [item.id for item in match_media_items(db, movie, None)] == [film_item.json()["id"]]
    finally:
        db.close()


def test_jellyfin_status_webhook_url_omits_query_token(client):
    login(client)
    _configure(client)
    status = client.get("/api/v1/integrations/jellyfin", headers=csrf(client))
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["webhook_url"].endswith("/api/v1/integrations/jellyfin/webhook")
    assert "token=" not in (body["webhook_url"] or "")
    assert "webhook_token" not in body
    assert body["webhook_token_set"] is True
    assert body["webhook_token_preview"].endswith("alue")
    assert body["webhook_token_preview"] != "webhook-secret-value"
    assert body["webhook_token_header"] == "X-MediaTracker-Token"
    assert "webhook-secret-value" not in status.text


def test_jellyfin_webhook_token_reveal_regenerate_and_journal(client):
    login(client)
    _configure(client)
    revealed = client.post(
        "/api/v1/integrations/jellyfin/webhook-token/reveal",
        headers=csrf(client),
    )
    assert revealed.status_code == 200, revealed.text
    assert revealed.headers.get("cache-control") == "no-store"
    assert revealed.json()["webhook_token"] == "webhook-secret-value"

    regen = client.post(
        "/api/v1/integrations/jellyfin/webhook-token/regenerate",
        headers=csrf(client),
    )
    assert regen.status_code == 200, regen.text
    assert regen.headers.get("cache-control") == "no-store"
    new_token = regen.json()["webhook_token"]
    assert new_token != "webhook-secret-value"
    assert len(new_token) >= 32

    old = client.post(
        "/api/v1/integrations/jellyfin/webhook?token=webhook-secret-value",
        json={"NotificationType": "PlaybackStop", "ItemType": "Episode", "PlayedToCompletion": True},
    )
    assert old.status_code == 401

    accepted = client.post(
        f"/api/v1/integrations/jellyfin/webhook?token={new_token}",
        json={"NotificationType": "PlaybackStop", "ItemType": "Episode", "PlayedToCompletion": True},
    )
    assert accepted.status_code == 200, accepted.text

    journal = client.get("/api/v1/events", params={"category": "jellyfin."})
    assert journal.status_code == 200, journal.text
    dumped = json.dumps(journal.json())
    assert "webhook-secret-value" not in dumped
    assert new_token not in dumped


def test_webhook_and_poll_sync_watched_state(client, app):
    login(client)
    _configure(client)
    media = _series(client)
    episode = _episode(client, media["id"], 1, 2)
    ignored = client.post(
        "/api/v1/integrations/jellyfin/webhook?token=nope",
        json={"NotificationType": "PlaybackStop", "ItemType": "Episode", "PlayedToCompletion": True},
    )
    assert ignored.status_code == 401
    partial = client.post(
        "/api/v1/integrations/jellyfin/webhook?token=webhook-secret-value",
        json={
            "NotificationType": "PlaybackStop",
            "ItemType": "Episode",
            "ItemId": "jf-1",
            "Provider_tmdb": "900101",
            "SeasonNumber": 1,
            "EpisodeNumber": 2,
            "PlayedToCompletion": False,
            "UserId": "user-1",
        },
    )
    assert partial.status_code == 200
    assert partial.json()["updated"] == 0
    other = client.post(
        "/api/v1/integrations/jellyfin/webhook?token=webhook-secret-value",
        json={
            "NotificationType": "PlaybackStop",
            "ItemType": "Episode",
            "ItemId": "jf-1",
            "Provider_tmdb": "900101",
            "SeasonNumber": 1,
            "EpisodeNumber": 2,
            "PlayedToCompletion": True,
            "UserId": "someone-else",
        },
    )
    assert other.json()["updated"] == 0
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
            "UserId": "user-1",
        },
    )
    assert done.status_code == 200, done.text
    assert done.json()["updated"] == 1
    body = client.get(f"/api/v1/media/{media['id']}/items").json()
    assert body["seasons"][0]["episodes"][0]["watched"] is True
    cleared = client.post(
        "/api/v1/integrations/jellyfin/webhook",
        headers={"X-MediaTracker-Token": "webhook-secret-value"},
        json={
            "NotificationType": "UserDataSaved",
            "ItemType": "Episode",
            "ItemId": "jf-1",
            "Provider_tmdb": "900101",
            "SeasonNumber": 1,
            "EpisodeNumber": 2,
            "Played": False,
            "UserId": "user-1",
        },
    )
    assert cleared.json()["updated"] == 1
    body = client.get(f"/api/v1/media/{media['id']}/items").json()
    assert body["seasons"][0]["episodes"][0]["watched"] is False

    fake = _Jellyfin(
        [
            {
                "Id": "jf-1",
                "Type": "Episode",
                "ParentIndexNumber": 1,
                "IndexNumber": 2,
                "ProviderIds": {"Tmdb": "900101"},
                "Path": "/srv/media/Series/Exemple (2024)/Season 01/Exemple (2024) - S01E02.mkv",
                "UserData": {"Played": True},
            }
        ]
    )
    now = datetime(2026, 10, 11, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        maybe_sync_jellyfin(db, app.state.settings, now=now, client=fake)
        row = db.get(MediaItem, episode["id"])
        assert row.watched is True
        assert row.jellyfin_item_id == "jf-1"
        assert row.state == "watched"
        fake.items[0]["UserData"] = {"Played": False}
        maybe_sync_jellyfin(db, app.state.settings, now=now + timedelta(minutes=30), client=fake)
        db.refresh(row)
        assert row.watched is False
        assert row.state == "missing"
    finally:
        db.close()
    board = client.get("/api/v1/dashboard").json()
    assert any(card["media_id"] == media["id"] and card["total"] >= 1 for card in board["progress"])


def test_library_refresh_is_debounced(client, app):
    login(client)
    _configure(client)
    fake = _Jellyfin()
    now = datetime(2026, 10, 11, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        schedule_library_refresh(db, now)
        schedule_library_refresh(db, now + timedelta(seconds=20))
        db.commit()
        maybe_sync_jellyfin(db, app.state.settings, now=now + timedelta(seconds=30), client=fake)
        assert fake.refreshed == 0
        maybe_sync_jellyfin(db, app.state.settings, now=now + timedelta(seconds=60), client=fake)
        assert fake.refreshed == 1
        maybe_sync_jellyfin(db, app.state.settings, now=now + timedelta(minutes=10), client=fake)
        assert fake.refreshed == 1
        assert fake.closed is False
    finally:
        db.close()


def test_connection_probe(client, monkeypatch):
    login(client)

    class _Probe:
        def system_info(self) -> dict:
            return {"ServerName": "Salon", "Version": "10.10.7"}

        def users(self) -> list[dict]:
            return [{"Id": "user-1", "Name": "nico"}]

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "mediatracker.api.v1.jellyfin.build_client",
        lambda url, api_key, user_id: _Probe(),
    )
    result = client.post(
        "/api/v1/integrations/jellyfin/test",
        json={"url": "http://jellyfin.local:8096", "api_key": "jellyfin-test-key", "user_id": "user-1"},
        headers=csrf(client),
    )
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["ok"] is True
    assert body["server_name"] == "Salon"
    assert "nico" in body["message"]
    assert "jellyfin-test-key" not in body["message"]


def test_tmdb_keeps_season_zero_and_french_backdrop():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/tv/7"):
            return httpx.Response(
                200,
                json={
                    "name": "Exemple",
                    "overview": "Résumé en français.",
                    "poster_path": "/poster.jpg",
                    "backdrop_path": "/backdrop.jpg",
                    "first_air_date": "2024-01-02",
                    "status": "Returning Series",
                    "seasons": [
                        {"season_number": 0, "name": "Spéciaux", "air_date": "2024-12-01", "episode_count": 1, "id": 8},
                        {"season_number": 1, "name": "Saison 1", "air_date": "2024-01-02", "episode_count": 1, "id": 9},
                    ],
                },
            )
        number = path.rsplit("/", 1)[-1]
        return httpx.Response(
            200,
            json={
                "episodes": [
                    {
                        "episode_number": 1,
                        "name": "Hors-série" if number == "0" else "Pilote",
                        "air_date": "2024-12-01" if number == "0" else "2024-01-02",
                        "id": 80 if number == "0" else 81,
                    }
                ]
            },
        )

    client = TmdbClient("token", transport=httpx.MockTransport(handler))
    try:
        title = client.fetch(7, "series")
    finally:
        client.close()
    assert title.overview == "Résumé en français."
    assert title.backdrop_url.endswith("/backdrop.jpg")
    assert [season.number for season in title.seasons] == [0, 1]
    assert title.seasons[0].episodes[0].title == "Hors-série"


def test_apply_watches_updates_every_episode_sharing_the_file(client, app):
    db = app.state.session_factory()
    try:
        media = Media(type="series", title="Exemple", year=2024, tmdb_id=900101, created_at=datetime.now(timezone.utc))
        db.add(media)
        db.flush()
        path = "/data/media/Series/Exemple (2024)/Season 01/Exemple (2024) - S01E01-E02.mkv"
        first = MediaItem(media_id=media.id, season=1, episode=1, file_path=path, state="downloaded", downloaded=True)
        second = MediaItem(media_id=media.id, season=1, episode=2, file_path=path, state="downloaded", downloaded=True)
        db.add(first)
        db.add(second)
        db.commit()
        watch = parse_webhook(
            {
                "NotificationType": "PlaybackStop",
                "ItemType": "Episode",
                "ItemId": "multi",
                "Path": path,
                "PlayedToCompletion": True,
            }
        )
        assert apply_watches(db, [watch], None, datetime(2026, 10, 11, tzinfo=timezone.utc)) == 2
        assert first.watched is True
        assert second.watched is True
    finally:
        db.close()


def test_items_query_uses_valid_fields_and_falls_back_after_a_400():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        fields = request.url.params.get("Fields")
        if fields == "ProviderIds,Path,ParentId":
            return httpx.Response(400, text='{"Error":"Invalid field"} Token=super-secret-key')
        if fields == "ProviderIds":
            return httpx.Response(
                200,
                json={
                    "Items": [
                        {
                            "Type": "Episode",
                            "Id": "ep",
                            "SeriesId": "series-1",
                            "SeriesName": "Rue Démo",
                            "ProductionYear": 2025,
                            "ParentIndexNumber": 1,
                            "IndexNumber": 1,
                            "ProviderIds": {"Tmdb": "900003"},
                            "UserData": {"Played": True},
                        }
                    ],
                    "TotalRecordCount": 1,
                },
            )
        return httpx.Response(500, text="unexpected")

    client = JellyfinClient(
        "http://jellyfin.example",
        "key-secret",
        "user-1",
        transport=httpx.MockTransport(handler),
    )
    try:
        rows = client.user_items("user-1")
    finally:
        client.close()
    assert rows[0]["SeriesName"] == "Rue Démo"
    assert rows[0]["ProviderIds"]["Tmdb"] == "900003"
    first = calls[0]
    assert str(first.url).startswith("http://jellyfin.example/Users/user-1/Items")
    assert first.headers["Authorization"].startswith('MediaBrowser Client="MediaTracker"')
    assert "Token=" in first.headers["Authorization"]
    assert "SeriesId" not in (first.url.params.get("Fields") or "")
    assert "SeriesName" not in (first.url.params.get("Fields") or "")
    assert "ProductionYear" not in (first.url.params.get("Fields") or "")
    assert calls[1].url.params.get("Fields") == "ProviderIds"


def test_jellyfin_error_keeps_the_response_body_and_hides_the_token():
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(400, text="champ refusé Token=super-secret-key")

    client = JellyfinClient(
        "http://jellyfin.example",
        "key-secret",
        "user-1",
        transport=httpx.MockTransport(handler),
    )
    try:
        try:
            client.user_items("user-1")
        except JellyfinError as exc:
            message = exc.message
        else:
            raise AssertionError("expected JellyfinError")
    finally:
        client.close()
    assert "champ refusé" in message
    assert "super-secret-key" not in message
    assert "400" in message


class _FicheJellyfin:
    def __init__(self, *_args, **_kwargs) -> None:
        return None

    def users(self) -> list[dict]:
        return [{"Id": "user-1"}]

    def user_items(self, user_id: str) -> list[dict]:
        assert user_id == "user-1"
        return [
            {
                "Type": "Episode",
                "Id": "ep",
                "SeriesId": "series-jf",
                "SeriesName": "Show",
                "ParentIndexNumber": 1,
                "IndexNumber": 1,
                "ProviderIds": {},
                "UserData": {"Played": False},
            }
        ]

    def series_index(self, user_id: str) -> dict:
        assert user_id == "user-1"
        return {"series-jf": {"Name": "Show", "ProductionYear": 2024, "ProviderIds": {"Tmdb": "321"}}}

    def close(self) -> None:
        return None


def test_fiche_routes_match_the_frontend_calls(client, monkeypatch):
    """GET and POST with no query and no body, the same way the fiche does."""
    login(client)
    _configure(client)
    created = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Show", "year": 2024, "tmdb_id": 321},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    media_id = created.json()["id"]
    added = client.post(
        f"/api/v1/media/{media_id}/items",
        json={"season": 1, "episode": 1, "title": "Un"},
        headers=csrf(client),
    )
    assert added.status_code == 201, added.text
    monkeypatch.setattr("mediatracker.integrations.jellyfin.JellyfinClient", _FicheJellyfin)
    diag = client.get(f"/api/v1/media/{media_id}/jellyfin")
    assert diag.status_code == 200, diag.text
    assert diag.json()["configured"] is True
    sync = client.post(f"/api/v1/media/{media_id}/jellyfin/sync", headers=csrf(client))
    assert sync.status_code == 200, sync.text
    body = sync.json()
    assert body["series_name"] == "Show"
    assert "Jellyfin" in body["detail"]


def test_fiche_shows_the_jellyfin_body_instead_of_a_bare_400(client, monkeypatch):
    login(client)
    _configure(client)
    created = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Show", "year": 2024},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    media_id = created.json()["id"]

    class _Broken(_FicheJellyfin):
        def user_items(self, user_id: str) -> list[dict]:
            del user_id
            raise JellyfinError("BAD_RESPONSE", "Jellyfin a répondu 400. Invalid field.")

    monkeypatch.setattr("mediatracker.integrations.jellyfin.JellyfinClient", _Broken)
    diag = client.get(f"/api/v1/media/{media_id}/jellyfin")
    assert diag.status_code == 502, diag.text
    assert diag.json()["detail"] == "Jellyfin a répondu 400. Invalid field."
    sync = client.post(f"/api/v1/media/{media_id}/jellyfin/sync", headers=csrf(client))
    assert sync.status_code == 502, sync.text
    assert "Invalid field" in sync.json()["detail"]
