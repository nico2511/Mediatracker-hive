"""Jellyfin web links use the configured URL and never the API key."""

from datetime import datetime, timezone

from tests.conftest import csrf, login

from mediatracker.core.jellyfin_web import jellyfin_home_url, jellyfin_web_url, playable_item_id
from mediatracker.db.models import Media, MediaItem, Setting


def test_item_and_home_urls():
    assert jellyfin_web_url("http://jellyfin.lan:8096/", "abc def") == (
        "http://jellyfin.lan:8096/web/index.html#!/details?id=abc%20def&context=home"
    )
    assert jellyfin_web_url("", "abc") is None
    assert jellyfin_web_url("http://jellyfin.lan:8096", "  ") is None
    assert jellyfin_home_url("http://jellyfin.lan:8096/") == "http://jellyfin.lan:8096/web/index.html"
    assert jellyfin_home_url(None) is None


def test_series_id_wins_over_the_first_episode():
    media = Media(type="series", title="Exemple", year=2024, created_at=datetime.now(timezone.utc))
    media.jellyfin_series_id = "series-1"
    episode = MediaItem(media_id=1, season=1, episode=1, jellyfin_item_id="ep-1")
    assert playable_item_id(media, [episode]) == "series-1"
    media.jellyfin_series_id = None
    assert playable_item_id(media, [episode]) == "ep-1"


def test_fiche_and_menu_expose_the_web_client(client, app):
    login(client)
    created = client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Archipel Pilote (FRENCH)", "year": 2021},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    assert created.json()["title"] == "Archipel Pilote"
    assert created.json()["jellyfin_web_url"] is None

    db = app.state.session_factory()
    try:
        now = datetime.now(timezone.utc)
        row = db.get(Setting, "jellyfin_url")
        if row is None:
            db.add(Setting(key="jellyfin_url", value="http://jellyfin.lan:8096/", is_secret=False, updated_at=now))
        else:
            row.value = "http://jellyfin.lan:8096/"
            row.updated_at = now
        media = db.get(Media, created.json()["id"])
        assert media is not None
        item = MediaItem(
            media_id=media.id,
            season=None,
            episode=None,
            title="Archipel Pilote",
            downloaded=False,
            watched=False,
            source_available=False,
            keep=False,
            state="missing",
            jellyfin_item_id="jf-archipel",
        )
        db.add(item)
        db.commit()
    finally:
        db.close()

    fiche = client.get(f"/api/v1/media/{created.json()['id']}")
    assert fiche.status_code == 200, fiche.text
    assert fiche.json()["jellyfin_web_url"] == (
        "http://jellyfin.lan:8096/web/index.html#!/details?id=jf-archipel&context=home"
    )
    menu = client.get("/api/v1/integrations/jellyfin/web")
    assert menu.status_code == 200, menu.text
    assert menu.json()["url"] == "http://jellyfin.lan:8096/web/index.html"
    assert "api" not in menu.json()["url"]
