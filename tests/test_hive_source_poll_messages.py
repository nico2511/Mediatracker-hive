"""Libellés journal du sondage Hive selon le type de fiche (film vs série)."""

from __future__ import annotations

from datetime import date

from tests.test_hive_t6 import NOW, _enable, _ruche, _seed_catalogue_row

from mediatracker.db.models import Media, MediaItem
from mediatracker.hive.source_poll import run_hive_follow_poll

pytest_plugins = ["tests.test_hive_t5"]


def test_hive_follow_poll_film_propose_message(client):
    _enable(client)
    db = client.app.state.session_factory()
    settings = client.app.state.settings
    rid = _ruche(client)
    media = Media(
        type="film",
        title="Film fictif",
        year=2099,
        tmdb_id=9901,
        followed=True,
        new_episode_policy="propose",
        catch_up_pending=True,
        pref_quality="HD",
        pref_language="VFF",
    )
    db.add(media)
    db.flush()
    item = MediaItem(media_id=media.id, season=None, episode=None, watched=False, state="missing")
    db.add(item)
    db.commit()
    _seed_catalogue_row(
        db,
        cle={"type": "film", "tmdb_id": 9901, "version": "1080p+VFF"},
        version="1080p+VFF",
        sha="a1" * 32,
        joignable=True,
        ruche_hex=rid.hex(),
    )
    report = run_hive_follow_poll(db, media, settings, force=True, now=NOW)
    assert report is not None
    assert report.message == "Film disponible, en attente de confirmation."
    db.close()


def test_hive_follow_poll_series_propose_message(client):
    _enable(client)
    db = client.app.state.session_factory()
    settings = client.app.state.settings
    rid = _ruche(client)
    media = Media(
        type="series",
        title="Serie fictive",
        year=2099,
        tmdb_id=9902,
        followed=True,
        new_episode_policy="propose",
        catch_up_pending=True,
        pref_quality="HD",
        pref_language="VFF",
    )
    db.add(media)
    db.flush()
    item = MediaItem(
        media_id=media.id,
        season=1,
        episode=1,
        watched=False,
        state="missing",
        air_date=date(2020, 1, 1),
    )
    db.add(item)
    db.commit()
    _seed_catalogue_row(
        db,
        cle={"type": "series", "tmdb_id": 9902, "saison": 1, "episode": 1, "version": "1080p+VFF"},
        version="1080p+VFF",
        sha="b2" * 32,
        joignable=True,
        ruche_hex=rid.hex(),
    )
    report = run_hive_follow_poll(db, media, settings, force=True, now=NOW)
    assert report is not None
    assert report.message == "1 épisode(s) repéré(s), en attente de confirmation."
    db.close()


def test_hive_follow_poll_film_no_slots_message(client):
    _enable(client)
    db = client.app.state.session_factory()
    settings = client.app.state.settings
    media = Media(
        type="film",
        title="Film fictif",
        year=2099,
        tmdb_id=9903,
        followed=True,
        new_episode_policy="propose",
    )
    db.add(media)
    db.flush()
    db.add(
        MediaItem(
            media_id=media.id,
            season=None,
            episode=None,
            state="missing",
            downloaded=True,
        )
    )
    db.commit()
    report = run_hive_follow_poll(db, media, settings, force=True, now=NOW)
    assert report is not None
    assert report.message == "Aucun film attendu pour le moment."
    db.close()