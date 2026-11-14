"""Le « prochain épisode » d'une série est toujours à venir (jamais une date passée, S00 en dernier recours)."""

from datetime import date, timedelta

from tests.conftest import csrf, login

from mediatracker.core.tracking import EpisodeView, resolve_next_episode
from mediatracker.db.models import Media, MediaItem
from mediatracker.integrations.tmdb_media import fill_next_episode

TODAY = date(2026, 11, 1)


def _item(season, episode, air, title="Épisode factice"):
    return MediaItem(season=season, episode=episode, title=title, air_date=air, state="missing")


def _view(season, episode, air, watched=False):
    return EpisodeView(
        season=season,
        episode=episode,
        title=f"Épisode {season}x{episode}",
        air_date=air,
        downloaded=False,
        watched=watched,
        source_available=False,
    )


def test_serie_terminee_sans_prochain_episode():
    row = Media(type="series", title="Série factice terminée")
    episodes = [
        _item(0, 1, date(2023, 6, 1), "Spécial factice"),
        _item(1, 1, date(2023, 9, 1)),
        _item(1, 2, date(2023, 9, 8)),
    ]
    fill_next_episode(row, episodes, today=TODAY)
    assert row.next_episode_season is None
    assert row.next_episode_number is None
    assert row.next_air_date is None
    nxt = resolve_next_episode(
        media_type="series",
        season=None,
        episode=None,
        title=None,
        air_date=None,
        episodes=[_view(0, 1, date(2023, 6, 1)), _view(1, 1, date(2023, 9, 1)), _view(1, 2, date(2023, 9, 8))],
        today=TODAY,
    )
    assert nxt is None


def test_special_passe_et_episode_futur_donne_l_episode_futur():
    row = Media(type="series", title="Série factice en cours")
    future = TODAY + timedelta(days=10)
    episodes = [
        _item(0, 1, date(2023, 6, 1), "Spécial factice"),
        _item(2, 2, TODAY - timedelta(days=7)),
        _item(2, 3, future, "Suite factice"),
    ]
    fill_next_episode(row, episodes, today=TODAY)
    assert (row.next_episode_season, row.next_episode_number, row.next_air_date) == (2, 3, future)


def test_special_futur_ignore_si_un_episode_regulier_est_annonce():
    row = Media(type="series", title="Série factice")
    episodes = [_item(0, 4, TODAY + timedelta(days=2)), _item(3, 1, TODAY + timedelta(days=30))]
    fill_next_episode(row, episodes, today=TODAY)
    assert (row.next_episode_season, row.next_episode_number) == (3, 1)
    only_special = Media(type="series", title="Série factice")
    fill_next_episode(only_special, [_item(0, 4, TODAY + timedelta(days=2))], today=TODAY)
    assert (only_special.next_episode_season, only_special.next_episode_number) == (0, 4)


def test_valeur_stockee_passee_masquee_et_remplacee():
    views = [_view(0, 1, date(2023, 6, 1)), _view(2, 3, TODAY + timedelta(days=5))]
    nxt = resolve_next_episode(
        media_type="series",
        season=0,
        episode=1,
        title="Spécial factice",
        air_date=date(2023, 6, 1),
        episodes=views,
        today=TODAY,
    )
    assert nxt is not None and (nxt.season, nxt.episode) == (2, 3)
    hidden = resolve_next_episode(
        media_type="series",
        season=0,
        episode=1,
        title=None,
        air_date=date(2023, 6, 1),
        episodes=[_view(0, 1, date(2023, 6, 1))],
        today=TODAY,
    )
    assert hidden is None


def test_valeur_stockee_passee_recalculee_par_fill():
    row = Media(type="series", title="Série factice")
    row.next_episode_season, row.next_episode_number, row.next_air_date = 0, 1, date(2023, 6, 1)
    future = TODAY + timedelta(days=3)
    fill_next_episode(row, [_item(0, 1, date(2023, 6, 1)), _item(2, 3, future)], today=TODAY)
    assert (row.next_episode_season, row.next_episode_number, row.next_air_date) == (2, 3, future)


def test_film_garde_sa_date_de_sortie():
    row = Media(type="film", title="Film factice")
    fill_next_episode(row, [MediaItem(title="Film factice", air_date=date(2020, 1, 1), state="missing")], today=TODAY)
    assert row.next_air_date == date(2020, 1, 1)


def test_api_masque_un_prochain_episode_passe(client):
    login(client)
    created = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Série factice API", "series_status": "ended"},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    media_id = created.json()["id"]
    past = date.today() - timedelta(days=900)
    client.post(
        f"/api/v1/media/{media_id}/items",
        json={"season": 0, "episode": 1, "title": "Spécial factice", "air_date": past.isoformat()},
        headers=csrf(client),
    )
    patched = client.patch(
        f"/api/v1/media/{media_id}",
        json={"next_episode_season": 0, "next_episode_number": 1, "next_air_date": past.isoformat()},
        headers=csrf(client),
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["next_episode"] is None
    assert client.get(f"/api/v1/media/{media_id}").json()["next_episode"] is None
