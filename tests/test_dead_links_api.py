"""Dead links exposed on the media fiche."""

from datetime import datetime, timezone

from tests.conftest import csrf, login

from mediatracker.db.models import Link, Media, MediaItem, Provider

NOW = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)


def _film_with_dead_link(db) -> tuple[int, int]:
    media = Media(type="film", title="Brume Azur", year=2026, followed=True)
    db.add(media)
    db.flush()
    item = MediaItem(media_id=media.id, season=None, episode=None, title="Brume Azur", state="missing")
    db.add(item)
    db.flush()
    provider = db.query(Provider).filter(Provider.kind == "source", Provider.type == "manual").one()
    link = Link(
        media_item_id=item.id,
        provider_id=provider.id,
        kind="hoster",
        url="https://rapidgator.net/file/brume-azur",
        hoster="rapidgator",
        external_id="brume-azur-dead",
        dead=True,
        last_error="Lien mort",
        discovered_at=NOW,
    )
    db.add(link)
    db.commit()
    return media.id, link.id


def test_dead_links_empty(client, app):
    login(client)
    created = client.post(
        "/api/v1/media",
        json={
            "type": "film",
            "title": "Vide",
            "year": 2026,
            "new_episode_policy": "auto",
            "pref_quality": "HD",
            "pref_language": "VFF",
        },
        headers=csrf(client),
    )
    media_id = created.json()["id"]
    response = client.get(f"/api/v1/media/{media_id}/dead-links")
    assert response.status_code == 200
    assert response.json() == []


def test_dead_links_lists_rows(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        media_id, link_id = _film_with_dead_link(db)
    finally:
        db.close()

    response = client.get(f"/api/v1/media/{media_id}/dead-links")
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == link_id
    assert body[0]["hoster"] == "rapidgator"
    assert body[0]["last_error"] == "Lien mort"
    assert "rapidgator" in body[0]["url"]
