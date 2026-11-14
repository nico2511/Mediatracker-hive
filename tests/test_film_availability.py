"""Disponibilité film, recherche auto, accueil « À voir »."""

from datetime import date, datetime, timedelta, timezone

from tests.conftest import csrf, login

from mediatracker.core.film_availability import compute_film_availability, film_not_yet_available
from mediatracker.core.polling import EpisodeAir, PollSubject, should_poll_source
from mediatracker.core.source_poll import expected_slots, poll_found_message
from mediatracker.db.models import LibraryFile, Media, MediaItem
from mediatracker.integrations.tmdb import TmdbTitle

TODAY = date(2026, 11, 2)
NOW = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)


def _release_dates(*blocks: tuple[str, int, str]) -> dict:
    grouped: dict[str, list[dict]] = {}
    for country, kind, day in blocks:
        grouped.setdefault(country, []).append({"type": kind, "release_date": f"{day}T00:00:00.000Z"})
    return {"results": [{"iso_3166_1": country, "release_dates": rows} for country, rows in grouped.items()]}


def test_compute_digital_fr_then_physical_then_any_country():
    payload = _release_dates(("FR", 3, "2026-06-01"), ("FR", 4, "2026-09-15"))
    available, theatrical, source = compute_film_availability(payload, date(2026, 6, 1))
    assert theatrical == date(2026, 6, 1)
    assert available == date(2026, 9, 15)
    assert source == "digital_fr"

    physical_only = _release_dates(("FR", 3, "2026-06-01"), ("FR", 5, "2026-10-01"))
    available, _t, source = compute_film_availability(physical_only, date(2026, 6, 1))
    assert available == date(2026, 10, 1)
    assert source == "physical_fr"

    foreign = _release_dates(("US", 3, "2026-05-01"), ("DE", 4, "2026-08-20"))
    available, _t, source = compute_film_availability(foreign, date(2026, 5, 1), country="FR")
    assert available == date(2026, 8, 20)
    assert source == "digital_any"


def test_theatrical_only_before_digital_window():
    payload = _release_dates(("FR", 3, "2026-06-01"), ("FR", 4, "2026-12-01"))
    available, _t, source = compute_film_availability(payload, date(2026, 6, 1), fallback_months=4)
    assert available == date(2026, 12, 1)
    assert source == "digital_fr"
    today = date(2026, 8, 1)
    assert today < available
    media = Media(type="film", title="Exemple Salle", followed=True, next_air_date=available)
    assert film_not_yet_available(media, today=today)


def test_theatrical_fallback_months():
    payload = _release_dates(("FR", 3, "2026-01-10"))
    available, theatrical, source = compute_film_availability(
        payload,
        date(2026, 1, 10),
        fallback_months=4,
    )
    assert theatrical == date(2026, 1, 10)
    assert available == date(2026, 5, 10)
    assert source == "theatrical_fallback"


def test_poll_found_message_film_uses_film_wording():
    film = Media(type="film", title="Exemple")
    series = Media(type="series", title="Série Démo")
    assert poll_found_message(film, episodes=0, queued=0, auto=True) == "La page ne contient pas le film attendu."
    assert poll_found_message(film, episodes=1, queued=1, auto=True) == "Film disponible, mis en file."
    assert poll_found_message(film, episodes=1, queued=0, auto=False) == "Film disponible, en attente de confirmation."
    nothing = "La page ne contient pas les épisodes attendus."
    assert poll_found_message(series, episodes=0, queued=0, auto=True) == nothing
    assert poll_found_message(series, episodes=2, queued=1, auto=True) == "2 épisodes disponibles, 1 mis en file."


def test_unchanged_poll_backoff_stretches_next_check(client, app):
    import random

    from mediatracker.core.source_poll import _finish
    from mediatracker.db.models import Media

    db = app.state.session_factory()
    try:
        media = Media(
            type="film",
            title="Exemple Backoff",
            followed=True,
            on_hold=False,
            new_episode_policy="auto",
            next_air_date=date(2026, 1, 1),
            last_source_poll_at=NOW - timedelta(days=2),
            last_poll_result="nothing",
            last_poll_message="Rien.",
            poll_unchanged_streak=2,
            created_at=NOW,
        )
        db.add(media)
        db.commit()
        report = _finish(
            db,
            media,
            app.state.settings,
            NOW,
            random.Random(0),
            result="nothing",
            message="Rien.",
            episodes=0,
            queued=0,
            proposed=0,
            failed=False,
        )
        assert report.next_check_at is not None
        assert report.next_check_at > NOW + timedelta(hours=20)
    finally:
        db.close()


def test_film_poll_after_availability_without_air_window():
    interval = timedelta(hours=12)
    kwargs = dict(now=NOW, interval=interval, ongoing_only=True, window_days=3)
    subject = PollSubject(
        followed=True,
        on_hold=False,
        media_type="film",
        series_status=None,
        new_episode_policy="auto",
        last_source_poll_at=NOW - timedelta(days=40),
        next_source_poll_at=None,
        next_air_date=date(2026, 9, 1),
        episodes=(EpisodeAir(date(2026, 1, 1), downloaded=False),),
    )
    assert should_poll_source(subject, **kwargs) == (True, "film_missing")


def test_expected_slots_film_future_date_with_force():
    media = Media(
        type="film",
        title="Exemple Force",
        followed=True,
        on_hold=False,
        new_episode_policy="auto",
        next_air_date=date(2027, 6, 1),
    )
    item = MediaItem(media_id=1, title="Exemple Force")
    slots = expected_slots(media, [item], today=TODAY, window_days=3, force=True)
    assert len(slots) == 1
    assert slots[0].season is None


def test_expected_slots_film_ignores_scan_window():
    media = Media(
        type="film",
        title="Exemple Cinéma",
        followed=True,
        on_hold=False,
        new_episode_policy="auto",
        next_air_date=date(2026, 1, 1),
    )
    item = MediaItem(media_id=1, title="Exemple Cinéma", air_date=date(2026, 1, 1))
    slots = expected_slots(media, [item], today=TODAY, window_days=3, force=False)
    assert len(slots) == 1


def test_film_future_date_not_yet_available(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        media = Media(
            type="film",
            title="Exemple Futur",
            followed=True,
            on_hold=False,
            new_episode_policy="auto",
            next_air_date=date(2027, 3, 1),
            created_at=NOW,
        )
        db.add(media)
        db.flush()
        db.add(MediaItem(media_id=media.id, title="Exemple Futur", air_date=date(2027, 1, 1)))
        db.commit()
        media_id = media.id
    finally:
        db.close()
    body = client.get(f"/api/v1/media/{media_id}").json()
    assert body["film_not_yet_available"] is True
    assert "Pas encore disponible" in (body["film_status_line"] or "")
    assert body["poll"]["allowed"] is False
    assert body["poll"]["reason"] == "before_air_date"


def test_film_on_disk_with_future_date_is_not_unavailable(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        media = Media(
            type="film",
            title="Exemple Disque",
            followed=True,
            on_hold=False,
            new_episode_policy="auto",
            next_air_date=date(2027, 3, 1),
            film_availability_source="digital_fr",
            created_at=NOW,
        )
        db.add(media)
        db.flush()
        db.add(
            MediaItem(
                media_id=media.id,
                title="Exemple Disque",
                downloaded=True,
                file_path="/data/films/exemple-disque.mkv",
            )
        )
        db.commit()
        media_id = media.id
    finally:
        db.close()
    body = client.get(f"/api/v1/media/{media_id}").json()
    assert body["film_not_yet_available"] is False
    assert body["availability"] == "on_disk"


def test_to_watch_includes_unfollowed_with_present_file(client, app):
    login(client)
    ghost = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Exemple Non Suivi", "year": 2023},
        headers=csrf(client),
    ).json()
    db = app.state.session_factory()
    try:
        item = MediaItem(
            media_id=ghost["id"],
            season=1,
            episode=1,
            title="Pilote",
            downloaded=True,
            watched=False,
            downloaded_at=NOW,
        )
        db.add(item)
        db.flush()
        db.add(
            LibraryFile(
                path="/data/series/non-suivi.mkv",
                media_type="series",
                status="matched",
                media_item_id=item.id,
                seen_at=NOW,
            )
        )
        row = db.get(Media, ghost["id"])
        assert row is not None
        row.followed = False
        db.commit()
    finally:
        db.close()
    board = client.get("/api/v1/dashboard").json()
    assert any(group["media_title"] == "Exemple Non Suivi" for group in board["to_watch_groups"])


def test_to_watch_excludes_missing_library_file(client, app):
    login(client)
    series = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Exemple Manquant", "year": 2024},
        headers=csrf(client),
    ).json()
    db = app.state.session_factory()
    try:
        item = MediaItem(
            media_id=series["id"],
            season=1,
            episode=1,
            title="Pilote",
            downloaded=True,
            watched=False,
            downloaded_at=NOW,
        )
        db.add(item)
        db.flush()
        db.add(
            LibraryFile(
                path="/data/series/manquant.mkv",
                media_type="series",
                status="matched",
                media_item_id=item.id,
                seen_at=NOW,
                missing_at=NOW,
            )
        )
        db.commit()
    finally:
        db.close()
    board = client.get("/api/v1/dashboard").json()
    assert not any(group["media_title"] == "Exemple Manquant" for group in board["to_watch_groups"])


def test_to_watch_excludes_unfollowed_and_missing_files(client, app):
    login(client)
    series = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Exemple Suivi", "year": 2024},
        headers=csrf(client),
    ).json()
    ghost = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Exemple Fantôme", "year": 2023},
        headers=csrf(client),
    ).json()
    db = app.state.session_factory()
    try:
        present = MediaItem(
            media_id=series["id"],
            season=1,
            episode=1,
            title="Pilote",
            downloaded=True,
            watched=False,
            downloaded_at=NOW,
        )
        stale = MediaItem(
            media_id=ghost["id"],
            season=1,
            episode=1,
            title="Orphelin",
            downloaded=True,
            watched=False,
            downloaded_at=NOW,
        )
        db.add_all([present, stale])
        db.flush()
        db.add(
            LibraryFile(
                path="/data/series/exemple-pilote.mkv",
                media_type="series",
                status="matched",
                media_item_id=present.id,
                seen_at=NOW,
            )
        )
        ghost_media = db.get(Media, ghost["id"])
        assert ghost_media is not None
        ghost_media.followed = False
        db.commit()
    finally:
        db.close()

    board = client.get("/api/v1/dashboard").json()
    groups = board["to_watch_groups"]
    titles = {group["media_title"] for group in groups}
    assert "Exemple Suivi" in titles
    # Unfollowed but no matched library file on disk — still out of « À voir ».
    assert "Exemple Fantôme" not in titles


def test_apply_film_from_tmdb_sets_next_air_date(client, monkeypatch):
    login(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"tmdb_read_token": "tmdb-test-token"}},
        headers=csrf(client),
    )
    future_digital = _release_dates(("FR", 3, "2026-06-01"), ("FR", 4, "2026-12-01"))

    class FakeTmdb:
        def __init__(self, token: str, **_kwargs) -> None:
            del token, _kwargs

        def fetch(self, tmdb_id: int, media_type: str) -> TmdbTitle:
            del tmdb_id, media_type
            return TmdbTitle(
                tmdb_id=501,
                media_type="film",
                title="Exemple Numérique",
                year=2026,
                overview=None,
                poster_url=None,
                series_status=None,
                release_date=date(2026, 6, 1),
                seasons=(),
                release_dates=future_digital,
            )

        def external_imdb(self, tmdb_id: int, media_type: str):
            del tmdb_id, media_type
            return None

        def close(self):
            return None

    monkeypatch.setattr("mediatracker.integrations.tmdb.TmdbClient", FakeTmdb)
    created = client.post(
        "/api/v1/media",
        json={"type": "film", "tmdb_id": 501},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["film_not_yet_available"] is True
    assert body["poll"]["reason"] == "before_air_date"
    db = client.app.state.session_factory()
    try:
        row = db.get(Media, body["id"])
        assert row is not None
        assert row.next_air_date == date(2026, 12, 1)
    finally:
        db.close()


def test_apply_film_from_tmdb_keeps_date_when_source_null(client, app):
    from sqlalchemy import select

    from mediatracker.core.film_availability import apply_film_from_tmdb
    from mediatracker.integrations.tmdb import TmdbTitle

    db = app.state.session_factory()
    try:
        media = Media(
            type="film",
            title="Exemple Null",
            followed=True,
            tmdb_id=504,
            next_air_date=date(2026, 12, 1),
            film_availability_source=None,
        )
        db.add(media)
        db.commit()
        fetched = TmdbTitle(
            tmdb_id=504,
            media_type="film",
            title="Exemple Null",
            year=2026,
            overview=None,
            poster_url=None,
            series_status=None,
            release_date=date(2026, 6, 1),
            seasons=(),
            release_dates=None,
        )
        apply_film_from_tmdb(db, media, fetched, fallback_months=4)
        db.commit()
        row = db.scalar(select(Media).where(Media.id == media.id))
        assert row is not None
        assert row.next_air_date == date(2026, 12, 1)
        assert row.film_availability_source is None
    finally:
        db.close()


def test_apply_film_from_tmdb_keeps_digital_when_release_dates_missing(client, app):
    from sqlalchemy import select

    from mediatracker.core.film_availability import apply_film_from_tmdb
    from mediatracker.integrations.tmdb import TmdbTitle

    db = app.state.session_factory()
    try:
        media = Media(
            type="film",
            title="Exemple Conservé",
            followed=True,
            tmdb_id=502,
            next_air_date=date(2026, 12, 1),
            film_availability_source="digital_fr",
        )
        db.add(media)
        db.commit()
        fetched = TmdbTitle(
            tmdb_id=502,
            media_type="film",
            title="Exemple Conservé",
            year=2026,
            overview=None,
            poster_url=None,
            series_status=None,
            release_date=date(2026, 6, 1),
            seasons=(),
            release_dates=None,
        )
        apply_film_from_tmdb(db, media, fetched, fallback_months=4)
        db.commit()
        row = db.scalar(select(Media).where(Media.id == media.id))
        assert row is not None
        assert row.next_air_date == date(2026, 12, 1)
        assert row.film_availability_source == "digital_fr"
    finally:
        db.close()


def test_maybe_refresh_films_updates_availability(client, app, settings):
    from mediatracker.core.seasons import maybe_refresh_films
    from mediatracker.integrations.tmdb import TmdbTitle

    db = app.state.session_factory()
    try:
        media = Media(
            type="film",
            title="Exemple Refresh",
            followed=True,
            on_hold=False,
            tmdb_id=503,
            next_air_date=date(2026, 5, 10),
            film_availability_source="theatrical_fallback",
            tmdb_refreshed_at=None,
        )
        db.add(media)
        db.commit()
        future_digital = _release_dates(("FR", 3, "2026-06-01"), ("FR", 4, "2026-11-20"))

        class FakeTmdb:
            def fetch(self, tmdb_id: int, media_type: str) -> TmdbTitle:
                assert tmdb_id == 503 and media_type == "film"
                return TmdbTitle(
                    tmdb_id=503,
                    media_type="film",
                    title="Exemple Refresh",
                    year=2026,
                    overview=None,
                    poster_url=None,
                    series_status=None,
                    release_date=date(2026, 6, 1),
                    seasons=(),
                    release_dates=future_digital,
                )

            def close(self):
                return None

        updated = maybe_refresh_films(db, settings, now=NOW, client=FakeTmdb())
        assert updated == 1
        db.expire_all()
        row = db.get(Media, media.id)
        assert row is not None
        assert row.next_air_date == date(2026, 11, 20)
        assert row.film_availability_source == "digital_fr"
        assert row.tmdb_refreshed_at is not None
    finally:
        db.close()


def test_to_watch_includes_unfollowed_film_with_present_file(client, app):
    login(client)
    ghost = client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Exemple Film Non Suivi", "year": 2025},
        headers=csrf(client),
    ).json()
    db = app.state.session_factory()
    try:
        item = MediaItem(
            media_id=ghost["id"],
            title="Exemple Film Non Suivi",
            downloaded=True,
            watched=False,
            downloaded_at=NOW,
            file_path="/data/films/non-suivi.mkv",
        )
        db.add(item)
        db.flush()
        db.add(
            LibraryFile(
                path="/data/films/non-suivi.mkv",
                media_type="film",
                status="matched",
                media_item_id=item.id,
                seen_at=NOW,
            )
        )
        row = db.get(Media, ghost["id"])
        assert row is not None
        row.followed = False
        db.commit()
    finally:
        db.close()
    board = client.get("/api/v1/dashboard").json()
    assert any(group["media_title"] == "Exemple Film Non Suivi" for group in board["to_watch_groups"])


def test_to_watch_excludes_film_missing_library_file(client, app):
    login(client)
    film = client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Exemple Film Manquant", "year": 2025},
        headers=csrf(client),
    ).json()
    db = app.state.session_factory()
    try:
        item = MediaItem(
            media_id=film["id"],
            title="Exemple Film Manquant",
            downloaded=True,
            watched=False,
            downloaded_at=NOW,
            file_path="/data/films/manquant.mkv",
        )
        db.add(item)
        db.flush()
        db.add(
            LibraryFile(
                path="/data/films/manquant.mkv",
                media_type="film",
                status="matched",
                media_item_id=item.id,
                seen_at=NOW,
                missing_at=NOW,
            )
        )
        db.commit()
    finally:
        db.close()
    board = client.get("/api/v1/dashboard").json()
    assert not any(group["media_title"] == "Exemple Film Manquant" for group in board["to_watch_groups"])


def test_film_watched_without_date_is_not_polled():
    interval = timedelta(hours=12)
    kwargs = dict(now=NOW, interval=interval, ongoing_only=True, window_days=3)
    subject = PollSubject(
        followed=True,
        on_hold=False,
        media_type="film",
        series_status=None,
        new_episode_policy="auto",
        last_source_poll_at=NOW - timedelta(days=40),
        next_source_poll_at=None,
        next_air_date=None,
        episodes=(EpisodeAir(date(2026, 1, 1), downloaded=False, watched=True),),
    )
    assert should_poll_source(subject, **kwargs) == (False, "outside_air_window")
