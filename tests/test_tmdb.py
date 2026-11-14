from datetime import date, timedelta

from tests.conftest import csrf, login

from mediatracker.integrations.tmdb import (
    TmdbClient,
    TmdbEpisode,
    TmdbHit,
    TmdbSeason,
    TmdbTitle,
    parse_tmdb_url,
)


def test_release_by_region_picks_fr_us_types():
    from mediatracker.integrations.tmdb_enrichment import release_by_region

    payload = {
        "results": [
            {
                "iso_3166_1": "FR",
                "release_dates": [
                    {"type": 3, "release_date": "2020-03-01T00:00:00.000Z"},
                    {"type": 4, "release_date": "2020-06-01T00:00:00.000Z"},
                ],
            },
            {
                "iso_3166_1": "US",
                "release_dates": [{"type": 3, "release_date": "2020-02-14T00:00:00.000Z"}],
            },
        ]
    }
    regions = release_by_region(payload)
    assert regions["FR"]["theatrical"] == "2020-03-01"
    assert regions["FR"]["digital"] == "2020-06-01"
    assert regions["US"]["theatrical"] == "2020-02-14"


def test_parse_tmdb_url():
    series = parse_tmdb_url("https://www.themoviedb.org/tv/900101-chimie-demo")
    assert series.tmdb_id == 900101
    assert series.media_type == "series"
    film = parse_tmdb_url("https://www.themoviedb.org/fr/movie/901002")
    assert film.tmdb_id == 901002
    assert film.media_type == "film"


def test_glance_lists_season_numbers_without_fetching_episodes():
    client = TmdbClient("token")
    calls: list[str] = []

    def fake_get(path, params=None):
        del params
        calls.append(path)
        return {
            "id": 1,
            "name": "Orbite Démo",
            "first_air_date": "2022-09-21",
            "overview": "Rebelle.",
            "poster_path": "/a.jpg",
            "seasons": [
                {"season_number": 1, "name": "Saison 1", "episode_count": 12},
                {"season_number": 0, "name": "Spéciaux", "episode_count": 1},
                {"season_number": "nope"},
            ],
        }

    client._get = fake_get  # type: ignore[method-assign]
    try:
        hit = client.glance(1, "series")
    finally:
        client.close()
    assert calls == ["/tv/1"]
    assert [(season.number, season.name, season.episode_count) for season in hit.seasons] == [
        (0, "Spéciaux", 1),
        (1, "Saison 1", 12),
    ]


def test_follow_without_token_keeps_manual_title(client):
    login(client)
    created = client.post(
        "/api/v1/media",
        json={"title": "Avant la sortie", "year": 2027, "tmdb_url": "https://www.themoviedb.org/tv/900101"},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["tmdb_id"] == 900101
    assert body["title"] == "Avant la sortie"
    assert body["type"] == "series"
    assert body["poll"]["reason"] == "no_air_date"
    missing = client.post(
        "/api/v1/media",
        json={"tmdb_url": "https://www.themoviedb.org/movie/1"},
        headers=csrf(client),
    )
    assert missing.status_code == 422


def test_follow_from_tmdb_fills_episodes_and_waits_for_air_date(client, monkeypatch):
    login(client)
    saved = client.patch(
        "/api/v1/settings",
        json={"values": {"tmdb_read_token": "tmdb-test-token"}},
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text
    air = date.today() + timedelta(days=21)

    class FakeTmdb:
        def __init__(self, token: str, **_kwargs) -> None:
            assert token == "tmdb-test-token"

        def fetch(self, tmdb_id: int, media_type: str) -> TmdbTitle:
            assert tmdb_id == 99
            assert media_type == "series"
            return TmdbTitle(
                tmdb_id=99,
                media_type="series",
                title="Demain",
                year=2026,
                overview="Pas encore en ligne.",
                poster_url="https://image.tmdb.org/t/p/w500/poster.jpg",
                series_status="returning",
                release_date=air,
                seasons=(
                    TmdbSeason(
                        number=1,
                        name="Saison 1",
                        overview=None,
                        air_date=air,
                        episode_count=1,
                        tmdb_id=5,
                        episodes=(TmdbEpisode(1, 1, "Pilote", air, 50),),
                    ),
                ),
            )

        def search(self, query: str, media_type: str) -> list[TmdbHit]:
            return [
                TmdbHit(
                    tmdb_id=99,
                    media_type=media_type,
                    title="Demain",
                    year=2026,
                    overview=None,
                    poster_url=None,
                )
            ]

        def close(self) -> None:
            return None

    def _borrow_tmdb(token: str, _factory) -> tuple[FakeTmdb, bool]:
        """Bypass ``shared_tmdb`` (real ``TmdbClient`` + ``_client``) so fakes stay isolated."""
        return FakeTmdb(token), True

    for path in (
        "mediatracker.integrations.tmdb.borrow_tmdb",
        "mediatracker.api.v1.metadata.borrow_tmdb",
        "mediatracker.api.v1.media.borrow_tmdb",
        "mediatracker.core.follow.borrow_tmdb",
    ):
        monkeypatch.setattr(path, _borrow_tmdb)
    found = client.get("/api/v1/metadata/tmdb/search", params={"q": "Demain", "type": "series"})
    assert found.status_code == 200, found.text
    assert found.json()["items"][0]["tmdb_id"] == 99
    created = client.post("/api/v1/media", json={"tmdb_id": 99, "type": "series"}, headers=csrf(client))
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["title"] == "Demain"
    assert body["poster_url"].endswith("poster.jpg")
    assert body["poll"]["allowed"] is False
    assert body["poll"]["reason"] == "before_air_date"
    assert body["next_episode"]["air_date"] == air.isoformat()
    episodes = client.get(f"/api/v1/media/{body['id']}/items").json()
    assert episodes["seasons"][0]["episodes"][0]["title"] == "Pilote"
