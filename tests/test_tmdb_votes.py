from mediatracker.core.tmdb_votes import exposed_tmdb_vote_average
from mediatracker.db.models import Media
from tests.conftest import csrf, login


def test_exposed_vote_average_null_without_votes():
    assert exposed_tmdb_vote_average(7.4, 0) is None
    assert exposed_tmdb_vote_average(7.4, None) == 7.4


def test_exposed_vote_average_null_when_zero_score():
    assert exposed_tmdb_vote_average(0.0, 12) is None
    assert exposed_tmdb_vote_average(None, 12) is None


def test_exposed_vote_average_keeps_valid():
    assert exposed_tmdb_vote_average(8.1, 1240) == 8.1


def test_media_api_hides_average_when_vote_count_zero(client, app):
    login(client)
    created = client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Fiche votes zéro", "year": 2099},
        headers=csrf(client),
    )
    assert created.status_code == 201
    media_id = created.json()["id"]
    db = app.state.session_factory()
    try:
        row = db.get(Media, media_id)
        row.tmdb_vote_average = 7.5
        row.tmdb_vote_count = 0
        db.commit()
    finally:
        db.close()
    detail = client.get(f"/api/v1/media/{media_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["tmdb_vote_average"] is None
    assert body["tmdb_vote_count"] == 0