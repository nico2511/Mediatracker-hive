"""« Disponible, pas encore téléchargé » skips watched, unfollowed, and obtained rows."""

from datetime import datetime, timezone

from tests.conftest import csrf, login

from mediatracker.db.models import Download, LibraryFile, Media, MediaItem, Provider, SourceBinding


def _offer(client, *, type_: str, title: str, **item):
    created = client.post(
        "/api/v1/media",
        json={"type": type_, "title": title, "year": 2024},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    media_id = created.json()["id"]
    body = {"source_available": True, "title": title}
    body.update(item)
    episode = client.post(f"/api/v1/media/{media_id}/items", json=body, headers=csrf(client))
    assert episode.status_code == 201, episode.text
    return media_id, episode.json()["id"]


def _ids(client) -> set[int]:
    board = client.get("/api/v1/dashboard")
    assert board.status_code == 200, board.text
    return {card["item_id"] for card in board.json()["available"]}


def test_available_keeps_only_episodes_still_worth_downloading(client, app):
    login(client)
    ready_media, ready = _offer(client, type_="series", title="Prêt", season=1, episode=1)
    _watched_media, watched = _offer(client, type_="series", title="Déjà vu", season=1, episode=2)
    _idle_media, idle = _offer(client, type_="series", title="Plus suivi", season=1, episode=3)
    season_media, wanted = _offer(client, type_="series", title="Saisons", season=1, episode=1)
    skipped_row = client.post(
        f"/api/v1/media/{season_media}/items",
        json={"season": 2, "episode": 1, "title": "Hors suivi", "source_available": True},
        headers=csrf(client),
    )
    assert skipped_row.status_code == 201, skipped_row.text
    skipped = skipped_row.json()["id"]
    film_media, film_ready = _offer(client, type_="film", title="À prendre")
    obtained_media, obtained = _offer(client, type_="film", title="Déjà obtenu")
    shelved_media, shelved = _offer(client, type_="film", title="Sur le disque")

    db = app.state.session_factory()
    try:
        db.get(MediaItem, watched).watched = True
        db.get(MediaItem, watched).watch_source = "auto"
        db.get(Media, idle).followed = False
        provider = db.query(Provider).filter(Provider.type == "wawacity").one()
        db.add(
            SourceBinding(
                media_id=season_media,
                provider_id=provider.id,
                external_ref="saisons-s1",
                season=1,
                active=True,
            )
        )
        db.get(Media, obtained_media).last_poll_result = "obtained"
        db.get(MediaItem, shelved).file_path = "/data/Films/Sur le disque.mkv"
        db.add(
            LibraryFile(
                path="/data/Films/Sur le disque.mkv",
                size=8,
                parsed_title="Sur le disque",
                media_type="film",
                media_id=shelved_media,
                media_item_id=shelved,
                status="matched",
                seen_at=datetime.now(timezone.utc),
            )
        )
        db.add(
            Download(
                media_item_id=ready,
                state="downloading",
                created_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
    finally:
        db.close()

    offered = _ids(client)
    assert wanted in offered
    assert film_ready in offered
    assert ready not in offered
    assert watched not in offered
    assert idle not in offered
    assert skipped not in offered
    assert obtained not in offered
    assert shelved not in offered

    db = app.state.session_factory()
    try:
        db.query(Download).filter(Download.media_item_id == ready).delete()
        db.commit()
    finally:
        db.close()
    assert ready in _ids(client)
