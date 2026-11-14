from datetime import date, datetime, timedelta, timezone

from tests.conftest import csrf, login

from mediatracker.core.polling import EpisodeAir, PollSubject, should_poll_source
from mediatracker.core.tracking import tmdb_page_url
from mediatracker.db.models import Download, LibraryFile, Media, MediaItem
from mediatracker.events import record_library_imported
from mediatracker.events.homeassistant import format_homeassistant
from mediatracker.events.ntfy import format_ntfy


def _series(**overrides):
    base = dict(
        followed=True,
        on_hold=False,
        media_type="series",
        series_status="returning",
        new_episode_policy="propose",
        last_source_poll_at=None,
        next_source_poll_at=None,
        next_air_date=None,
        episodes=(),
    )
    base.update(overrides)
    return PollSubject(**base)


def test_poll_policy_avoids_spam_and_uses_air_dates():
    now = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
    interval = timedelta(hours=12)
    kwargs = dict(now=now, interval=interval, ongoing_only=True, window_days=3)

    assert should_poll_source(_series(on_hold=True), **kwargs) == (False, "on_hold")
    assert should_poll_source(_series(series_status="ended"), **kwargs) == (False, "series_finished")
    assert should_poll_source(_series(series_status="canceled"), **kwargs) == (False, "series_finished")
    assert should_poll_source(_series(new_episode_policy="off"), **kwargs) == (False, "policy_off")
    assert should_poll_source(_series(media_type="other"), **kwargs) == (False, "not_series")
    assert should_poll_source(_series(media_type="film"), **kwargs) == (True, "no_air_date")
    premiere = should_poll_source(
        _series(media_type="film", next_air_date=date(2026, 12, 1)),
        **kwargs,
    )
    assert premiere == (False, "before_air_date")
    released = should_poll_source(
        _series(media_type="film", next_air_date=date(2026, 10, 11)),
        **kwargs,
    )
    assert released == (True, "film_missing")

    recent = should_poll_source(_series(last_source_poll_at=now - timedelta(hours=2)), **kwargs)
    assert recent == (False, "interval")

    far = should_poll_source(_series(next_air_date=date(2026, 12, 1)), **kwargs)
    assert far == (False, "before_air_date")

    tomorrow = should_poll_source(_series(next_air_date=date(2026, 10, 12)), **kwargs)
    assert tomorrow == (False, "before_air_date")

    today = should_poll_source(_series(next_air_date=date(2026, 10, 11)), **kwargs)
    assert today == (True, "air_window")

    recent_air = should_poll_source(
        _series(episodes=(EpisodeAir(date(2026, 10, 10), downloaded=False),)),
        **kwargs,
    )
    assert recent_air == (True, "air_window")

    already = should_poll_source(
        _series(episodes=(EpisodeAir(date(2026, 10, 10), downloaded=True),)),
        **kwargs,
    )
    assert already == (True, "no_air_date")

    backoff = should_poll_source(
        _series(next_source_poll_at=now + timedelta(hours=1), next_air_date=date(2026, 10, 11)),
        **kwargs,
    )
    assert backoff == (False, "backoff")

    unknown = should_poll_source(_series(), **kwargs)
    assert unknown == (True, "no_air_date")

    obtained = should_poll_source(
        _series(
            media_type="film",
            new_episode_policy="auto",
            catch_up=True,
            next_air_date=date(2026, 10, 11),
            episodes=(EpisodeAir(date(2026, 10, 11), downloaded=True),),
        ),
        **kwargs,
    )
    assert obtained == (False, "film_obtained")
    on_disk = should_poll_source(
        _series(
            media_type="film",
            next_air_date=date(2026, 10, 11),
            episodes=(EpisodeAir(date(2026, 10, 11), downloaded=False, on_disk=True),),
        ),
        **kwargs,
    )
    assert on_disk == (False, "film_obtained")


def test_tracking_api_dashboard_and_ntfy(client, app):
    login(client)
    created = client.post(
        "/api/v1/media",
        json={
            "type": "series",
            "title": "Exemple",
            "year": 2024,
            "tmdb_id": 99,
            "series_status": "returning",
            "new_episode_policy": "auto",
            "overview": "Une série.",
            "poster_url": "https://image.tmdb.org/t/p/w500/x.jpg",
        },
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    media = created.json()
    assert media["tmdb_url"] == "https://www.themoviedb.org/tv/99"
    assert media["auto_download"] is True
    assert media["progress"] == {"watched": 0, "total": 0}
    assert tmdb_page_url("film", 5) == "https://www.themoviedb.org/movie/5"

    held = client.patch(f"/api/v1/media/{media['id']}", json={"on_hold": True}, headers=csrf(client))
    assert held.json()["on_hold"] is True

    today = date.today()
    first = client.post(
        f"/api/v1/media/{media['id']}/items",
        json={"season": 1, "episode": 1, "title": "Pilote", "downloaded": True, "air_date": today.isoformat()},
        headers=csrf(client),
    )
    assert first.status_code == 201, first.text
    assert first.json()["downloaded"] is True
    assert first.json()["watched"] is False
    db = app.state.session_factory()
    try:
        pilot = db.get(MediaItem, first.json()["id"])
        assert pilot is not None
        pilot.file_path = "/data/series/exemple-pilote.mkv"
        db.add(
            LibraryFile(
                path=pilot.file_path,
                media_type="series",
                status="matched",
                media_item_id=pilot.id,
                seen_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
    finally:
        db.close()
    second = client.post(
        f"/api/v1/media/{media['id']}/items",
        json={
            "season": 1,
            "episode": 2,
            "title": "Suite",
            "source_available": True,
            "air_date": (today + timedelta(days=1)).isoformat(),
        },
        headers=csrf(client),
    )
    assert second.status_code == 201, second.text

    detail = client.get(f"/api/v1/media/{media['id']}").json()
    assert detail["progress"] == {"watched": 0, "total": 2}
    assert detail["available_not_downloaded"] == 1
    seasons = client.get(f"/api/v1/media/{media['id']}/items").json()["seasons"]
    assert seasons[0]["number"] == 1
    assert [episode["episode"] for episode in seasons[0]["episodes"]] == [1, 2]

    announced = client.patch(
        f"/api/v1/media/{media['id']}",
        json={
            "next_episode_season": 2,
            "next_episode_number": 1,
            "next_episode_title": "Retour",
            "next_air_date": (today + timedelta(days=2)).isoformat(),
        },
        headers=csrf(client),
    )
    assert announced.json()["next_episode"]["air_date"] == (today + timedelta(days=2)).isoformat()

    board = client.get("/api/v1/dashboard").json()
    assert any(card["episode"] == 1 and card["downloaded"] and not card["watched"] for card in board["to_watch"])
    upcoming_eps = {(card["season"], card["episode"]) for card in board["upcoming_week"]}
    assert (1, 1) in upcoming_eps or (1, 2) in upcoming_eps
    assert (2, 1) in upcoming_eps

    db = app.state.session_factory()
    try:
        now = datetime.now(timezone.utc)
        db.add(
            Download(
                media_item_id=second.json()["id"],
                state="downloading",
                progress=40,
                filename="suite.mkv",
                created_at=now,
            )
        )
        db.add(
            Download(
                media_item_id=first.json()["id"],
                state="failed",
                error_code="LINK_HOST_UNAVAILABLE",
                error="Hébergeur indisponible",
                created_at=now,
            )
        )
        record_library_imported(
            db,
            db.get(Media, media["id"]),
            db.get(MediaItem, first.json()["id"]),
            "/data/media/Series/Exemple/S01E01.mkv",
        )
        db.commit()
    finally:
        db.close()

    board = client.get("/api/v1/dashboard").json()
    assert board["downloads_active"][0]["state"] == "downloading"
    assert board["downloads_failed"][0]["error_code"] == "LINK_HOST_UNAVAILABLE"

    events = client.get("/api/v1/events", params={"type": "library.imported"}).json()
    payload = events["items"][0]["payload"]
    assert payload["path"].endswith("S01E01.mkv")
    assert payload["poster_url"] == "https://image.tmdb.org/t/p/w500/x.jpg"
    note = format_ntfy("library.imported", payload)
    assert note.title == "Nouvel épisode"
    assert "S01E01" in note.message
    assert "NAS" in note.message
    assert "Authorization" not in note.headers()
    assert note.headers("ntfy-token")["Authorization"] == "Bearer ntfy-token"
    ha_body = format_homeassistant("library.imported", payload)
    assert ha_body["event"] == "library.imported"
    assert ha_body["title"] == "Nouvel épisode"
    assert "S01E01" in ha_body["message"]
    assert ha_body["poster_url"] == payload["poster_url"]
    assert ha_body["media"]["tmdb_id"] == 99
    assert ha_body["episode"]["season"] == 1
    assert ha_body["episode"]["number"] == 1
    assert ha_body["episode"]["title"] == "Pilote"
    film = format_homeassistant(
        "library.imported",
        {"title": "Film", "media_type": "film", "media_id": 3, "item_id": 4, "path": "/data/media/Films/Film.mkv"},
    )
    assert film["title"] == "Film disponible"
    assert film["episode"]["number"] is None
    assert film["poster_url"] is None

    hook = client.post(
        "/api/v1/webhooks",
        json={
            "name": "salon",
            "url": "https://ntfy.example/mediatracker",
            "type": "ntfy",
            "events": ["library.imported"],
        },
        headers=csrf(client),
    )
    assert hook.status_code == 201, hook.text
    assert hook.json()["secret_set"] is False
    ha = client.post(
        "/api/v1/webhooks",
        json={
            "name": "Home Assistant",
            "url": "http://homeassistant.local:8123/api/webhook/mediatracker",
            "type": "homeassistant",
            "events": ["library.imported"],
        },
        headers=csrf(client),
    )
    assert ha.status_code == 201, ha.text
    assert ha.json()["type"] == "homeassistant"
    assert ha.json()["secret_set"] is False
    bad_url = client.post(
        "/api/v1/webhooks",
        json={
            "name": "mauvais",
            "url": "http://homeassistant.local:8123/api/events/mediatracker",
            "type": "homeassistant",
            "events": ["library.imported"],
        },
        headers=csrf(client),
    )
    assert bad_url.status_code == 422
    with_secret = client.post(
        "/api/v1/webhooks",
        json={
            "name": "secret",
            "url": "http://homeassistant.local:8123/api/webhook/autre",
            "type": "homeassistant",
            "events": ["library.imported"],
            "secret": "pas-de-hmac",
        },
        headers=csrf(client),
    )
    assert with_secret.status_code == 422
    assert "library.imported" in client.get("/api/v1/events/types").json()["events"]
    missing_secret = client.post(
        "/api/v1/webhooks",
        json={"name": "hmac", "url": "https://hooks.example/hook", "type": "generic", "events": ["library.imported"]},
        headers=csrf(client),
    )
    assert missing_secret.status_code == 422
