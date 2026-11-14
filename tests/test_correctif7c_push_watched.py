"""Correctif 7c — la poussée Jellyfin ne doit pas annuler un marquage local."""

from tests.conftest import csrf, login

from mediatracker.db.models import MediaItem

USER = "11111111-1111-1111-1111-111111111111"


def _configure_jellyfin_push(client) -> None:
    saved = client.patch(
        "/api/v1/settings",
        json={
            "values": {
                "jellyfin_url": "http://jellyfin.test",
                "jellyfin_api_key": "jellyfin-test-key",
                "jellyfin_user_id": USER,
                "jellyfin_push_watched": True,
            }
        },
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text


def test_mark_played_runtime_error_still_persists_watched(client, app, monkeypatch):
    login(client)
    _configure_jellyfin_push(client)
    created = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Série factice push", "year": 2025},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    media_id = created.json()["id"]
    episode = client.post(
        f"/api/v1/media/{media_id}/items",
        json={"season": 1, "episode": 1, "title": "Un"},
        headers=csrf(client),
    )
    assert episode.status_code == 201, episode.text
    item_id = episode.json()["id"]

    class _Client:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def close(self) -> None:
            return None

        def mark_played(self, user_id: str, jellyfin_id: str) -> None:
            raise RuntimeError("Jellyfin indisponible")

        def mark_unplayed(self, user_id: str, jellyfin_id: str) -> None:
            raise RuntimeError("Jellyfin indisponible")

    monkeypatch.setattr("mediatracker.integrations.jellyfin.JellyfinClient", _Client)
    monkeypatch.setattr(
        "mediatracker.integrations.jellyfin_sync.resolve_configured_user",
        lambda _db, _client, _configured, _moment=None: USER,
    )
    db = app.state.session_factory()
    try:
        row = db.get(MediaItem, item_id)
        assert row is not None
        row.jellyfin_item_id = "jf-ep-push"
        db.commit()
    finally:
        db.close()

    marked = client.post(
        f"/api/v1/media/{media_id}/watch",
        json={"item_id": item_id},
        headers=csrf(client),
    )
    assert marked.status_code == 200, marked.text
    assert marked.json()["updated"] == 1

    db = app.state.session_factory()
    try:
        row = db.get(MediaItem, item_id)
        assert row is not None
        assert row.watched is True
        assert row.watch_source == "manual"
    finally:
        db.close()
