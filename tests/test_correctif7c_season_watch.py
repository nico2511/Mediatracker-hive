"""Correctif 7c — marquage saison via l'API fiche (série à suivi limité)."""

from sqlalchemy import select
from tests.conftest import csrf, login

from mediatracker.db.models import Media, MediaItem


def _episode_rows(app, media_id: int) -> list[MediaItem]:
    db = app.state.session_factory()
    try:
        return list(
            db.scalars(
                select(MediaItem)
                .where(MediaItem.media_id == media_id)
                .order_by(MediaItem.season, MediaItem.episode)
            ).all()
        )
    finally:
        db.close()


def test_mark_season_watched_respects_season_scope_with_tracked_seasons(client, app):
    login(client)
    created = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Série factice suivi", "year": 2025, "seasons": [2]},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    media_id = created.json()["id"]
    db = app.state.session_factory()
    try:
        media = db.get(Media, media_id)
        assert media is not None
        assert media.tracked_seasons == [2]
    finally:
        db.close()

    for season, episode in ((1, 1), (1, 2), (2, 1), (2, 2)):
        added = client.post(
            f"/api/v1/media/{media_id}/items",
            json={"season": season, "episode": episode, "title": f"S{season:02d}E{episode:02d}"},
            headers=csrf(client),
        )
        assert added.status_code == 201, added.text

    marked = client.post(
        f"/api/v1/media/{media_id}/watch",
        json={"season": 1},
        headers=csrf(client),
    )
    assert marked.status_code == 200, marked.text
    assert marked.json()["updated"] == 2

    rows = _episode_rows(app, media_id)
    s1 = [row for row in rows if row.season == 1]
    s2 = [row for row in rows if row.season == 2]
    assert len(s1) == 2 and all(row.watched for row in s1)
    assert len(s2) == 2 and not any(row.watched for row in s2)

    undone = client.post(
        f"/api/v1/media/{media_id}/watch",
        json={"season": 1, "undo": True},
        headers=csrf(client),
    )
    assert undone.status_code == 200, undone.text
    assert undone.json()["updated"] == 2

    rows = _episode_rows(app, media_id)
    assert not any(row.watched for row in rows)
