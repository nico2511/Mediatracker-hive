"""Jellyfin is the master for identification. A manual TMDB change is pushed back."""

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from sqlalchemy import select
from tests.conftest import csrf, login

from mediatracker.core.naming import ID_CONFLICT
from mediatracker.db.models import (
    IdentityChange,
    JellyfinLibraryItem,
    JellyfinPushJob,
    LibraryFile,
    Media,
    MediaItem,
    Setting,
)
from mediatracker.integrations import jellyfin_library as jellyfin_library_mod
from mediatracker.integrations.jellyfin import JellyfinClient
from mediatracker.library.identity_sync import note_manual_tmdb
from mediatracker.library.jellyfin_align import (
    LIBRARY_SYNCED,
    AlignError,
    _adopt_newer,
    _restore_watched,
    apply_alignment,
    apply_renames,
    auto_rename_enabled,
    build_report,
    commit_renames,
    maybe_align_jellyfin,
    preview,
    refresh_cache,
)
from mediatracker.library.jellyfin_push import enqueue_identify, maybe_push_jellyfin
from mediatracker.library.scanner import scan_library

USER = "11111111-1111-1111-1111-111111111111"
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


def run_due_jellyfin_push(
    app,
    *,
    now: datetime | None = None,
    calls: list | None = None,
) -> None:
    """Force pending push jobs due at ``now`` (parcours use a fixed clock)."""
    moment = now or NOW
    idle_calls: list = [] if calls is None else calls
    db = _db(app)
    for job in db.scalars(select(JellyfinPushJob).where(JellyfinPushJob.state == "pending")).all():
        job.next_retry_at = moment
    db.commit()
    maybe_push_jellyfin(db, app.state.settings, now=moment, client=_client(_idle(idle_calls)))
    db.commit()


def _configure(client) -> None:
    saved = client.patch(
        "/api/v1/settings",
        json={
            "values": {
                "jellyfin_url": "http://jellyfin.test",
                "jellyfin_api_key": "jellyfin-test-key",
                "jellyfin_user_id": USER,
            }
        },
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text


def _series(client, title: str, tmdb_id: int | None, year: int = 2024) -> dict:
    body: dict = {"type": "series", "title": title, "year": year}
    if tmdb_id is not None:
        body["tmdb_id"] = tmdb_id
    created = client.post("/api/v1/media", json=body, headers=csrf(client))
    assert created.status_code == 201, created.text
    return created.json()


def _episode(client, media_id: int, season: int, episode: int) -> dict:
    created = client.post(
        f"/api/v1/media/{media_id}/items",
        json={"season": season, "episode": episode, "title": f"E{episode}"},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    return created.json()


def _db(app):
    return app.state.session_factory()


def _cache(db, **kwargs) -> None:
    now = kwargs.pop("synced_at", NOW)
    db.add(JellyfinLibraryItem(synced_at=now, **kwargs))
    db.commit()


def _client(handler) -> JellyfinClient:
    return JellyfinClient("http://jellyfin.test", "key", USER, transport=httpx.MockTransport(handler))


def _idle(calls: list, items: list[dict] | None = None, *, apply_status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, dict(request.url.params), request.content))
        path = request.url.path
        if path == "/ScheduledTasks":
            return httpx.Response(200, json=[{"Key": "RefreshLibrary", "State": "Idle"}])
        if path == "/Library/VirtualFolders":
            return httpx.Response(200, json=[])
        if path.endswith("/Items") and request.method == "GET":
            rows = items or []
            return httpx.Response(200, json={"Items": rows, "TotalRecordCount": len(rows)})
        if "/RemoteSearch/Apply/" in path:
            if apply_status >= 400:
                return httpx.Response(apply_status, json={"message": "no"})
            return httpx.Response(200, json={})
        if path.endswith("/Refresh") or path == "/Library/Refresh" or path == "/Library/Media/Updated":
            return httpx.Response(200, json={})
        if "/PlayedItems/" in path:
            return httpx.Response(204)
        if request.method == "POST" and "/Items/" in path:
            return httpx.Response(200, json={})
        return httpx.Response(404, json={})

    return handler


def test_auto_rename_defaults_off(client):
    login(client)
    listed = client.get("/api/v1/settings").json()["settings"]
    row = next(item for item in listed if item["key"] == "jellyfin_auto_rename")
    assert row["value"] is False


def test_scan_running_returns_no_changes(client, app):
    login(client)
    _configure(client)
    _series(client, "Exemple", 900101)
    db = _db(app)
    _cache(
        db,
        jellyfin_id="series-1",
        item_type="Series",
        name="Exemple",
        year=2024,
        series_id="series-1",
        tmdb_id=900101,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/ScheduledTasks"
        return httpx.Response(
            200,
            json=[{"Key": "RefreshLibrary", "State": "Running", "CurrentProgressPercentage": 40}],
        )

    jellyfin = _client(handler)
    try:
        report = preview(db, app.state.settings, jellyfin)
        assert report["scanning"] is True
        assert report["progress"] == 40
        assert report["attaches"] == []
        assert report["renames"] == []
        try:
            apply_alignment(db, app.state.settings, jellyfin, validated=True)
        except AlignError as exc:
            assert exc.code == "SCANNING"
        else:
            raise AssertionError("apply should refuse a running scan")
    finally:
        jellyfin.close()


def test_movie_attach_downloaded_and_watched(client, app):
    login(client)
    film = client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Archipel Pilote", "year": 2021, "tmdb_id": 900005},
        headers=csrf(client),
    )
    assert film.status_code == 201, film.text
    media_id = film.json()["id"]
    item = client.post(
        f"/api/v1/media/{media_id}/items",
        json={"title": "Archipel Pilote"},
        headers=csrf(client),
    )
    assert item.status_code == 201, item.text
    db = _db(app)
    _cache(
        db,
        jellyfin_id="movie-1",
        item_type="Movie",
        name="Archipel Pilote",
        year=2021,
        series_id="movie-1",
        tmdb_id=900005,
        path="/data/Films/Archipel Pilote.mkv",
        local_path="/data/Films/Archipel Pilote.mkv",
        played=True,
    )
    report = build_report(db, app.state.settings)
    assert report["reidentifies"] == []
    assert report["merges"] == []
    attach = report["attaches"][0]
    assert attach["media_id"] == film.json()["id"]
    assert attach["items"][0]["jellyfin_item_id"] == "movie-1"
    assert attach["items"][0]["downloaded"] is True
    assert attach["items"][0]["watched"] is True
    jellyfin = _client(_idle([]))
    try:
        apply_alignment(db, app.state.settings, jellyfin, validated=True, now=NOW)
    finally:
        jellyfin.close()
    db.commit()
    stored = db.get(MediaItem, item.json()["id"])
    media = db.get(Media, film.json()["id"])
    assert stored.jellyfin_item_id == "movie-1"
    assert stored.downloaded is True
    assert stored.watched is True
    assert media.jellyfin_series_id == "movie-1"
    assert media.identity_edited_at is None


def test_jellyfin_tmdb_wins_on_apply(client, app):
    login(client)
    media = _series(client, "Mauvais", 1)
    db = _db(app)
    _cache(
        db,
        jellyfin_id="series-9",
        item_type="Series",
        name="Bon titre",
        year=2020,
        series_id="series-9",
        tmdb_id=99,
    )
    row = db.get(Media, media["id"])
    row.jellyfin_series_id = "series-9"
    db.commit()
    report = build_report(db, app.state.settings)
    assert report["reidentifies"][0]["from_tmdb"] == 1
    assert report["reidentifies"][0]["to_tmdb"] == 99
    jellyfin = _client(_idle([]))
    try:
        apply_alignment(db, app.state.settings, jellyfin, validated=True, now=NOW)
    finally:
        jellyfin.close()
    db.commit()
    row = db.get(Media, media["id"])
    assert row.tmdb_id == 99
    assert row.title == "Bon titre"
    assert row.identity_edited_at is None


def test_duplicate_fiches_merge(client, app):
    login(client)
    first = _series(client, "Un", 900101)
    second = _series(client, "Deux", None)
    episode = _episode(client, second["id"], 1, 1)
    db = _db(app)
    other = db.get(Media, second["id"])
    other.tmdb_id = 900101
    db.commit()
    _cache(
        db,
        jellyfin_id="series-1",
        item_type="Series",
        name="Exemple",
        year=2024,
        series_id="series-1",
        tmdb_id=900101,
    )
    _cache(
        db,
        jellyfin_id="ep-1",
        item_type="Episode",
        name="Pilot",
        series_id="series-1",
        season=1,
        episode=1,
        played=True,
        local_path="/data/ep.mkv",
    )
    report = build_report(db, app.state.settings)
    merge = report["merges"][0]
    assert merge["keep_id"] == first["id"]
    assert merge["drop_ids"] == [second["id"]]
    jellyfin = _client(_idle([]))
    try:
        apply_alignment(db, app.state.settings, jellyfin, validated=True, now=NOW)
    finally:
        jellyfin.close()
    db.commit()
    assert db.get(Media, second["id"]) is None
    kept = db.get(MediaItem, episode["id"])
    assert kept.media_id == first["id"]
    assert kept.jellyfin_item_id == "ep-1"
    assert kept.downloaded is True
    assert kept.watched is True
    assert db.get(Media, first["id"]).jellyfin_series_id == "series-1"


def test_imdb_resolves_through_tmdb_find(client, app):
    login(client)
    _configure(client)
    media = _series(client, "Externe", None)
    db = _db(app)
    row = db.get(Media, media["id"])
    row.imdb_id = "tt424242"
    db.commit()
    calls: list = []

    def tmdb_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["external_source"] == "imdb_id"
        return httpx.Response(200, json={"movie_results": [], "tv_results": [{"id": 77}]})

    from mediatracker.integrations.tmdb import TmdbClient

    tmdb = TmdbClient("token", transport=httpx.MockTransport(tmdb_handler))
    raw = [
        {
            "Id": "series-7",
            "Type": "Series",
            "Name": "Externe",
            "ProductionYear": 2019,
            "ProviderIds": {"Imdb": "tt424242"},
        }
    ]
    jellyfin = _client(_idle(calls, raw))
    try:
        refresh_cache(db, app.state.settings, jellyfin, full=True, now=NOW, tmdb=tmdb)
    finally:
        jellyfin.close()
        tmdb.close()
    db.commit()
    report = build_report(db, app.state.settings)
    assert any(row["media_id"] == media["id"] and row["to_tmdb"] == 77 for row in report["attaches"])


def test_episode_matches_path_then_slot(client, app):
    login(client)
    media = _series(client, "Exemple", 900101)
    by_path = _episode(client, media["id"], 1, 2)
    by_slot = _episode(client, media["id"], 1, 3)
    db = _db(app)
    path_row = db.get(MediaItem, by_path["id"])
    path_row.file_path = "/library/Exemple/Season 01/custom.mkv"
    db.commit()
    _cache(
        db,
        jellyfin_id="series-1",
        item_type="Series",
        name="Exemple",
        year=2024,
        series_id="series-1",
        tmdb_id=900101,
    )
    _cache(
        db,
        jellyfin_id="by-path",
        item_type="Episode",
        series_id="series-1",
        season=1,
        episode=9,
        local_path="/library/Exemple/Season 01/custom.mkv",
        played=False,
    )
    _cache(
        db,
        jellyfin_id="by-slot",
        item_type="Episode",
        series_id="series-1",
        season=1,
        episode=3,
        local_path="/library/other.mkv",
        played=True,
    )
    report = build_report(db, app.state.settings)
    links = {row["jellyfin_item_id"]: row for row in report["attaches"][0]["items"]}
    assert links["by-path"]["item_id"] == by_path["id"]
    assert links["by-slot"]["item_id"] == by_slot["id"]
    assert links["by-slot"]["watched"] is True


def test_orphan_fiche_has_no_jellyfin_item(client, app):
    login(client)
    orphan = _series(client, "Seule", 5)
    known = _series(client, "Connue", 6)
    episode = _episode(client, orphan["id"], 1, 1)
    db = _db(app)
    row = db.get(MediaItem, episode["id"])
    row.downloaded = True
    row.file_path = "/library/seule.mkv"
    db.commit()
    _cache(
        db,
        jellyfin_id="series-6",
        item_type="Series",
        name="Connue",
        series_id="series-6",
        tmdb_id=6,
    )
    report = build_report(db, app.state.settings)
    assert [row["media_id"] for row in report["orphans"]] == [orphan["id"]]
    assert known["id"] not in [row["media_id"] for row in report["orphans"]]


def test_incremental_query_sends_watermark(client, app):
    login(client)
    _configure(client)
    db = _db(app)
    db.add(
        Setting(
            key="jellyfin.library_synced_at",
            value="2026-10-01T00:00:00+00:00",
            is_secret=False,
            updated_at=NOW,
        )
    )
    db.commit()
    calls: list = []
    jellyfin = _client(_idle(calls, []))
    try:
        refresh_cache(db, app.state.settings, jellyfin, full=False, now=NOW)
    finally:
        jellyfin.close()
    queries = [params for method, path, params, _body in calls if method == "GET" and path.endswith("/Items")]
    assert queries
    assert queries[0]["MinDateLastSaved"] == "2026-10-01T00:00:00+00:00"


def test_folder_scan_does_not_invent_a_fiche_jellyfin_owns(client, app):
    del client
    root = Path(app.state.settings.data_dir) / "media" / "Series"
    video = root / "Messy Name" / "episode.mkv"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"x")
    db = _db(app)
    db.add(
        JellyfinLibraryItem(
            jellyfin_id="ep-x",
            item_type="Episode",
            series_id="series-x",
            path=str(video),
            local_path=str(video.resolve()),
            season=1,
            episode=1,
            synced_at=NOW,
        )
    )
    db.commit()

    class Boom:
        def search(self, *_args, **_kwargs):
            raise AssertionError("TMDB ne doit pas identifier un fichier déjà connu de Jellyfin.")

        def close(self) -> None:
            return None

    scan_library(db, movies=root.parent / "Films", series=root, now=NOW, tmdb=Boom())
    db.commit()
    assert db.query(Media).count() == 0
    library = db.query(LibraryFile).one()
    assert library.status == "review"
    assert "Jellyfin" in (library.note or "")


def test_rename_uses_jellyfin_identity_then_refreshes(client, app):
    login(client)
    media = _series(client, "Exemple", 900101)
    episode = _episode(client, media["id"], 1, 2)
    root = Path(app.state.settings.data_dir) / "media" / "Series"
    src = root / "Mauvais dossier" / "Season 01" / "junk.mkv"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"video")
    side = src.with_suffix(".mkv.srt")
    side.write_text("a", encoding="utf-8")
    db = _db(app)
    row = db.get(MediaItem, episode["id"])
    row.file_path = str(src)
    row.downloaded = True
    row.watched = True
    row.jellyfin_item_id = "ep-1"
    media_row = db.get(Media, media["id"])
    media_row.jellyfin_series_id = "series-1"
    db.commit()
    _cache(
        db,
        jellyfin_id="series-1",
        item_type="Series",
        name="Exemple",
        year=2024,
        series_id="series-1",
        tmdb_id=900101,
    )
    _cache(
        db,
        jellyfin_id="ep-1",
        item_type="Episode",
        series_id="series-1",
        season=1,
        episode=2,
        local_path=str(src),
        played=True,
    )
    report = build_report(db, app.state.settings)
    move = report["renames"][0]
    assert move["blocked"] is None
    assert "[tmdbid-900101]" in move["dest"]
    assert "/Season 01/" in move["dest"].replace("\\", "/")
    assert move["dest"].endswith("Exemple (2024) - S01E02.mkv")
    calls: list = []
    jellyfin = _client(_idle(calls))
    try:
        result = apply_renames(db, app.state.settings, jellyfin, validated=True, now=NOW)
    finally:
        jellyfin.close()
    db.commit()
    assert result["moved"] == 2
    assert result["refresh"] == "item"
    assert Path(move["dest"]).is_file()
    assert not src.exists()
    paths = [path for _method, path, _params, _body in calls]
    assert any(path == "/Library/Media/Updated" for path in paths)
    assert any(path.endswith("/Refresh") for path in paths)
    stored = db.get(MediaItem, episode["id"])
    assert stored.file_path == move["dest"]
    assert stored.watched is True


def test_watched_is_restored_when_jellyfin_drops_it(client, app):
    login(client)
    _configure(client)
    media = _series(client, "Exemple", 900101)
    episode = _episode(client, media["id"], 1, 1)
    db = _db(app)
    row = db.get(MediaItem, episode["id"])
    row.jellyfin_item_id = "ep-1"
    row.watched = True
    db.commit()
    from mediatracker.db.models import JellyfinWatchSnapshot

    db.add(
        JellyfinWatchSnapshot(
            media_item_id=episode["id"],
            jellyfin_item_id="ep-1",
            played=True,
            path="/library/Exemple (2024) [tmdbid-900101]/Season 01/Exemple (2024) - S01E01.mkv",
            created_at=NOW,
        )
    )
    db.add(Setting(key="jellyfin.restore_pending", value="1", is_secret=False, updated_at=NOW))
    db.commit()
    calls: list = []
    raw = [
        {
            "Id": "ep-1",
            "Type": "Episode",
            "SeriesId": "series-1",
            "ParentIndexNumber": 1,
            "IndexNumber": 1,
            "Path": "/library/Exemple (2024) [tmdbid-900101]/Season 01/Exemple (2024) - S01E01.mkv",
            "UserData": {"Played": False},
        }
    ]
    jellyfin = _client(_idle(calls, raw))
    try:
        restored = _restore_watched(db, jellyfin, app.state.settings, NOW)
    finally:
        jellyfin.close()
    db.commit()
    assert restored == 1
    assert any("/PlayedItems/ep-1" in path for _method, path, _params, _body in calls)


def test_automatic_rename_stays_off_until_the_setting_is_on(client, app):
    login(client)
    _configure(client)
    media = _series(client, "Exemple", 900101)
    episode = _episode(client, media["id"], 1, 1)
    root = Path(app.state.settings.data_dir) / "media" / "Series"
    src = root / "Loose" / "Season 01" / "a.mkv"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"video")
    db = _db(app)
    assert auto_rename_enabled(db) is False
    item = db.get(MediaItem, episode["id"])
    item.file_path = str(src)
    item.downloaded = True
    media_row = db.get(Media, media["id"])
    media_row.jellyfin_series_id = "series-1"
    db.commit()
    _cache(
        db,
        jellyfin_id="series-1",
        item_type="Series",
        name="Exemple",
        year=2024,
        series_id="series-1",
        tmdb_id=900101,
    )
    jellyfin = _client(_idle([]))
    try:
        held = apply_renames(db, app.state.settings, jellyfin, validated=False, automatic=True, now=NOW)
        assert src.is_file()
        assert held["moved"] == 0
        saved = client.patch(
            "/api/v1/settings",
            json={"values": {"jellyfin_auto_rename": True}},
            headers=csrf(client),
        )
        assert saved.status_code == 200, saved.text
        db.expire_all()
        moved = apply_renames(db, app.state.settings, jellyfin, validated=False, automatic=True, now=NOW)
    finally:
        jellyfin.close()
    db.commit()
    assert moved["moved"] >= 1
    assert not src.is_file()


def test_manual_tmdb_override_is_queued_and_pushed(client, app):
    login(client)
    _configure(client)
    media = _series(client, "Exemple", 1)
    episode = _episode(client, media["id"], 1, 1)
    root = Path(app.state.settings.data_dir) / "media" / "Series"
    src = root / "Old (2024) [tmdbid-1]" / "Season 01" / "Old (2024) - S01E01.mkv"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"video")
    db = _db(app)
    item = db.get(MediaItem, episode["id"])
    item.file_path = str(src)
    item.watched = True
    row = db.get(Media, media["id"])
    row.jellyfin_series_id = "series-1"
    db.commit()
    _cache(
        db,
        jellyfin_id="series-1",
        item_type="Series",
        name="Jellyfin",
        year=2024,
        series_id="series-1",
        tmdb_id=1,
    )
    patched = client.patch(
        f"/api/v1/media/{media['id']}",
        json={"tmdb_id": 50, "tmdb_id_confirm": "keep_episodes"},
        headers=csrf(client),
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["tmdb_id"] == 50
    assert patched.json()["identity_edited_at"]
    again = client.patch(
        f"/api/v1/media/{media['id']}",
        json={"tmdb_id": 50, "tmdb_id_confirm": "keep_episodes"},
        headers=csrf(client),
    )
    assert again.status_code == 200, again.text
    db.expire_all()
    jobs = list(db.query(JellyfinPushJob).all())
    assert len(jobs) == 1
    assert jobs[0].state == "pending"
    assert jobs[0].tmdb_id == 50
    report = build_report(db, app.state.settings)
    assert report["reidentifies"] == []
    assert report["holds"][0]["tmdb_id"] == 50
    assert report["holds"][0]["jellyfin_tmdb"] == 1
    calls: list = []
    jellyfin = _client(_idle(calls))
    try:
        maybe_push_jellyfin(db, app.state.settings, now=datetime.now(timezone.utc), client=jellyfin)
    finally:
        jellyfin.close()
    db.expire_all()
    job = db.query(JellyfinPushJob).one()
    assert job.state == "done"
    paths = [path for method, path, params, _body in calls]
    assert any(path == "/Items/RemoteSearch/Apply/series-1" for path in paths)
    refresh = [params for method, path, params, _body in calls if path == "/Items/series-1/Refresh"]
    assert refresh and refresh[0]["ReplaceAllMetadata"] == "true"
    assert src.is_file()
    refused = [row for row in build_report(db, app.state.settings)["renames"] if row["src"] == str(src)]
    assert refused and refused[0]["blocked"] == "Identifiants différents."
    assert "[tmdbid-50]" in refused[0]["dest"]


def test_remote_search_falls_back_to_provider_ids(client, app):
    login(client)
    _configure(client)
    media = _series(client, "Exemple", 3)
    db = _db(app)
    row = db.get(Media, media["id"])
    row.jellyfin_series_id = "series-3"
    row.identity_edited_at = NOW
    db.commit()
    enqueue_identify(db, row, NOW, previous_tmdb=1)
    db.commit()
    calls: list = []
    jellyfin = _client(_idle(calls, apply_status=400))
    try:
        maybe_push_jellyfin(db, app.state.settings, now=NOW, client=jellyfin)
    finally:
        jellyfin.close()
    posts = [(method, path) for method, path, _params, _body in calls]
    assert ("POST", "/Items/RemoteSearch/Apply/series-3") in posts
    assert ("POST", "/Items/series-3") in posts


def test_push_retries_then_stays_idempotent(client, app, caplog):
    caplog.set_level(logging.INFO, logger="mediatracker.library.jellyfin_push")
    login(client)
    _configure(client)
    media = _series(client, "Exemple", 8)
    db = _db(app)
    row = db.get(Media, media["id"])
    row.jellyfin_series_id = "series-8"
    row.identity_edited_at = NOW
    db.commit()
    enqueue_identify(db, row, NOW, previous_tmdb=None)
    same = enqueue_identify(db, db.get(Media, media["id"]), NOW, previous_tmdb=None)
    db.commit()
    assert same.idempotency_key == f"{media['id']}:8"

    def down(_request: httpx.Request) -> httpx.Response:
        if _request.url.path == "/ScheduledTasks":
            return httpx.Response(200, json=[{"Key": "RefreshLibrary", "State": "Idle"}])
        return httpx.Response(500, json={})

    jellyfin = _client(down)
    try:
        maybe_push_jellyfin(db, app.state.settings, now=NOW, client=jellyfin)
    finally:
        jellyfin.close()
    db.expire_all()
    job = db.query(JellyfinPushJob).one()
    assert job.state == "pending"
    assert job.attempts == 1
    due = job.next_retry_at
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    assert due > NOW
    jellyfin = _client(down)
    try:
        maybe_push_jellyfin(db, app.state.settings, now=NOW + timedelta(seconds=1), client=jellyfin)
    finally:
        jellyfin.close()
    db.expire_all()
    assert db.query(JellyfinPushJob).one().attempts == 1
    assert "jellyfin push reportée" in caplog.text


def test_push_waits_out_a_library_scan(client, app, caplog):
    caplog.set_level(logging.INFO, logger="mediatracker.library.jellyfin_push")
    login(client)
    _configure(client)
    media = _series(client, "Exemple", 8)
    db = _db(app)
    row = db.get(Media, media["id"])
    row.jellyfin_series_id = "series-8"
    row.identity_edited_at = NOW
    db.commit()
    enqueue_identify(db, row, NOW, previous_tmdb=None)
    db.commit()

    def scanning(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/ScheduledTasks"
        return httpx.Response(
            200,
            json=[{"Key": "RefreshLibrary", "State": "Running", "CurrentProgressPercentage": 10}],
        )

    stuck = db.query(JellyfinPushJob).one()
    stuck.state = "running"
    stuck.next_retry_at = NOW - timedelta(minutes=11)
    db.commit()

    jellyfin = _client(scanning)
    try:
        maybe_push_jellyfin(db, app.state.settings, now=NOW, client=jellyfin)
    finally:
        jellyfin.close()
    db.close()
    fresh = _db(app)
    job = fresh.query(JellyfinPushJob).one()
    assert job.state == "pending"
    assert job.attempts == 0
    fresh.close()
    assert "jellyfin push en attente" in caplog.text


CALM_PASSES = 5


def test_maybe_align_calm_passes_skip_jellyfin_http(client, app, monkeypatch):
    login(client)
    _configure(client)
    db = _db(app)
    db.add(Setting(key=LIBRARY_SYNCED, value=NOW.isoformat(), is_secret=False, updated_at=NOW))
    db.commit()
    scan_calls = 0

    def counting_read_scan_status(_client):
        nonlocal scan_calls
        scan_calls += 1
        return jellyfin_library_mod.read_scan_status(_client)

    monkeypatch.setattr(jellyfin_library_mod, "read_scan_status", counting_read_scan_status)
    for step in range(CALM_PASSES):
        maybe_align_jellyfin(db, app.state.settings, now=NOW + timedelta(seconds=6 * step))
    assert scan_calls == 0, f"attendu 0 appel read_scan_status sur {CALM_PASSES} passes calmes, vu {scan_calls}"


def test_worker_remembers_a_scan_it_saw(client, app):
    login(client)
    _configure(client)
    db = _db(app)
    db.add(Setting(key="jellyfin.align_pending", value="1", is_secret=False, updated_at=NOW))
    db.add(Setting(key="jellyfin.refresh_fired_at", value=NOW.isoformat(), is_secret=False, updated_at=NOW))
    db.commit()

    def scanning(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/ScheduledTasks"
        return httpx.Response(
            200,
            json=[{"Key": "RefreshLibrary", "State": "Running", "CurrentProgressPercentage": 40}],
        )

    jellyfin = _client(scanning)
    try:
        maybe_align_jellyfin(db, app.state.settings, now=NOW, client=jellyfin)
    finally:
        jellyfin.close()
    db.close()

    fresh = _db(app)
    saw = fresh.get(Setting, "jellyfin.align_saw_scan")
    assert saw is not None and saw.value == "1"
    calls: list = []
    items = [
        {
            "Id": "series-1",
            "Type": "Series",
            "Name": "Exemple",
            "ProductionYear": 2024,
            "ProviderIds": {"Tmdb": "900101"},
        }
    ]
    idle = _client(_idle(calls, items))
    try:
        maybe_align_jellyfin(fresh, app.state.settings, now=NOW + timedelta(seconds=1), client=idle)
    finally:
        idle.close()
    fresh.expire_all()
    assert fresh.query(JellyfinLibraryItem).count() == 1
    pending = fresh.get(Setting, "jellyfin.align_pending")
    assert pending is not None and pending.value == ""
    fresh.close()


def test_align_route_reports_a_running_scan(client, app, monkeypatch):
    login(client)
    _configure(client)

    class Fake:
        def __init__(self, *_args, **_kwargs) -> None:
            return None

        def scheduled_tasks(self) -> list[dict]:
            return [{"Key": "RefreshLibrary", "State": "Running", "CurrentProgressPercentage": 15}]

        def virtual_folders(self) -> list[dict]:
            return []

        def close(self) -> None:
            return None

    monkeypatch.setattr("mediatracker.library.jellyfin_align.JellyfinClient", Fake)
    listed = client.get("/api/v1/library/jellyfin")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["scanning"] is True
    assert body["progress"] == 15
    assert body["attaches"] == []
    refused = client.post("/api/v1/library/jellyfin/align", json={"validated": True}, headers=csrf(client))
    assert refused.status_code == 409, refused.text
    assert app is not None


def test_jellyfin_edit_after_mediatracker_is_adopted(client, app):
    login(client)
    media = _series(client, "Exemple", 50)
    db = _db(app)
    row = db.get(Media, media["id"])
    row.jellyfin_series_id = "series-1"
    row.identity_edited_at = NOW
    row.identity_echo_tmdb = 50
    row.identity_echo_previous = 1
    row.identity_echo_at = NOW
    db.commit()
    later = NOW + timedelta(hours=1)
    _cache(
        db,
        jellyfin_id="series-1",
        item_type="Series",
        name="Autre",
        year=2021,
        series_id="series-1",
        tmdb_id=77,
        date_last_saved=later,
    )
    assert _adopt_newer(db, app.state.settings, later) == 1
    db.expire_all()
    row = db.get(Media, media["id"])
    assert row.tmdb_id == 77
    assert row.title == "Autre"
    edited = row.identity_edited_at
    if edited.tzinfo is None:
        edited = edited.replace(tzinfo=timezone.utc)
    assert edited == NOW
    change = db.query(IdentityChange).one()
    assert change.source == "jellyfin"
    assert change.old_tmdb == 50
    assert change.new_tmdb == 77
    assert change.undone_at is None
    pending = [job for job in db.query(JellyfinPushJob).all() if job.state in {"pending", "running"}]
    assert pending == []


def test_mediatracker_edit_after_jellyfin_is_pushed(client, app):
    login(client)
    media = _series(client, "Exemple", 1)
    db = _db(app)
    row = db.get(Media, media["id"])
    row.jellyfin_series_id = "series-1"
    db.commit()
    _cache(
        db,
        jellyfin_id="series-1",
        item_type="Series",
        name="Jellyfin",
        year=2020,
        series_id="series-1",
        tmdb_id=99,
        date_last_saved=NOW,
    )
    row = db.get(Media, media["id"])
    row.tmdb_id = 50
    note_manual_tmdb(db, row, NOW + timedelta(hours=2), previous_tmdb=1)
    db.commit()
    assert _adopt_newer(db, app.state.settings, NOW + timedelta(hours=2)) == 0
    db.expire_all()
    assert db.get(Media, media["id"]).tmdb_id == 50
    report = build_report(db, app.state.settings)
    assert report["reidentifies"] == []
    assert report["holds"][0]["tmdb_id"] == 50
    assert report["holds"][0]["jellyfin_tmdb"] == 99
    job = db.query(JellyfinPushJob).filter(JellyfinPushJob.state == "pending").one()
    assert job.tmdb_id == 50
    assert db.query(IdentityChange).one().source == "mediatracker"


def test_own_push_echo_is_ignored(client, app):
    login(client)
    media = _series(client, "Exemple", 50)
    db = _db(app)
    row = db.get(Media, media["id"])
    row.jellyfin_series_id = "series-1"
    row.identity_edited_at = NOW
    echo_at = NOW + timedelta(minutes=1)
    row.identity_echo_tmdb = 50
    row.identity_echo_previous = 1
    row.identity_echo_at = echo_at
    db.commit()
    _cache(
        db,
        jellyfin_id="series-1",
        item_type="Series",
        name="Ancien",
        year=2024,
        series_id="series-1",
        tmdb_id=1,
        date_last_saved=echo_at,
    )
    assert _adopt_newer(db, app.state.settings, echo_at) == 0
    db.expire_all()
    assert db.get(Media, media["id"]).tmdb_id == 50
    assert db.query(IdentityChange).count() == 0
    cached = db.get(JellyfinLibraryItem, "series-1")
    cached.tmdb_id = 50
    cached.name = "Exemple"
    cached.date_last_saved = echo_at + timedelta(minutes=5)
    db.commit()
    assert _adopt_newer(db, app.state.settings, echo_at + timedelta(minutes=5)) == 0
    db.expire_all()
    assert db.get(Media, media["id"]).tmdb_id == 50
    assert db.query(IdentityChange).count() == 0
    assert db.query(JellyfinPushJob).count() == 0


def test_undo_identity_restores_the_previous_id(client, app):
    login(client)
    media = _series(client, "Exemple", 1)
    db = _db(app)
    item = MediaItem(media_id=media["id"], title="E1", file_path="/data/undo/local.mkv")
    db.add(item)
    db.commit()
    db.close()
    patched = client.patch(
        f"/api/v1/media/{media['id']}",
        json={"tmdb_id": 50, "tmdb_id_confirm": "keep_episodes"},
        headers=csrf(client),
    )
    assert patched.status_code == 200, patched.text
    listed = client.get(f"/api/v1/media/{media['id']}/identity")
    assert listed.status_code == 200, listed.text
    change = listed.json()[0]
    assert change["source"] == "mediatracker"
    assert change["old_tmdb"] == 1
    assert change["new_tmdb"] == 50
    undone = client.post(f"/api/v1/media/{media['id']}/identity/{change['id']}/undo", headers=csrf(client))
    assert undone.status_code == 200, undone.text
    assert undone.json()["undone_at"]
    assert client.get(f"/api/v1/media/{media['id']}").json()["tmdb_id"] == 1
    db = _db(app)
    job = db.query(JellyfinPushJob).filter(JellyfinPushJob.state == "pending").one()
    assert job.tmdb_id == 1
    db.close()


def test_identity_webhook_rejects_a_bad_token(client, app):
    login(client)
    media = _series(client, "Exemple", 900101)
    refused = client.post(
        "/api/v1/integrations/jellyfin/webhook?token=nope",
        json={"NotificationType": "ItemUpdated", "Provider_tmdb": "99999", "ItemType": "Series"},
    )
    assert refused.status_code == 401
    db = _db(app)
    assert db.get(Setting, "jellyfin.identity_due") is None
    assert db.get(Media, media["id"]).tmdb_id == 900101
    assert db.query(JellyfinLibraryItem).count() == 0
    db.close()


def test_identity_webhook_schedules_a_read_and_ignores_the_body(client, app):
    login(client)
    _configure(client)
    saved = client.patch(
        "/api/v1/settings",
        json={"values": {"jellyfin_webhook_secret": "webhook-secret-value"}},
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text
    media = _series(client, "Exemple", 900101)
    db = _db(app)
    db.add(
        Setting(
            key="jellyfin.library_synced_at",
            value="2026-10-01T00:00:00+00:00",
            is_secret=False,
            updated_at=NOW,
        )
    )
    db.commit()
    accepted = client.post(
        "/api/v1/integrations/jellyfin/webhook?token=webhook-secret-value",
        json={
            "NotificationType": "ItemUpdated",
            "ItemType": "Series",
            "ItemId": "series-1",
            "Name": "Mensonge",
            "Provider_tmdb": "99999",
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["scheduled"] is True
    assert accepted.json()["updated"] == 0
    db.expire_all()
    assert db.get(Media, media["id"]).tmdb_id == 900101
    assert db.query(JellyfinLibraryItem).count() == 0
    assert db.get(Setting, "jellyfin.identity_due").value == "1"
    calls: list = []
    items = [
        {
            "Id": "series-1",
            "Type": "Series",
            "Name": "Exemple",
            "ProductionYear": 2024,
            "ProviderIds": {"Tmdb": "900101"},
        }
    ]
    jellyfin = _client(_idle(calls, items))
    try:
        maybe_align_jellyfin(db, app.state.settings, now=NOW, client=jellyfin)
    finally:
        jellyfin.close()
    queries = [params for method, path, params, _body in calls if method == "GET" and path.endswith("/Items")]
    assert queries
    assert queries[0]["MinDateLastSaved"] == "2026-10-01T00:00:00+00:00"
    db.expire_all()
    assert db.get(Media, media["id"]).tmdb_id == 900101
    assert db.query(JellyfinLibraryItem).one().tmdb_id == 900101
    assert db.get(Setting, "jellyfin.identity_due").value == ""
    db.close()


def test_lanterns_files_stay_when_jellyfin_series_name_differs(client, app):
    """Fichiers tagués [tmdbid-900103] : pas de déplacement vers la fiche Jellyfin [tmdbid-900102]."""
    login(client)
    _configure(client)
    media = _series(client, "Lanternes Démo", 900103, year=2026)
    first = _episode(client, media["id"], 1, 1)
    second = _episode(client, media["id"], 1, 2)
    root = Path(app.state.settings.data_dir) / "media" / "Series"
    folder = root / "Lanternes Démo (2026) [tmdbid-900103]" / "Season 01"
    folder.mkdir(parents=True)
    sources = []
    for episode, name in ((first, "S01E01"), (second, "S01E02")):
        src = folder / f"Lanternes Démo (2026) - {name}.mkv"
        src.write_bytes(b"video")
        sources.append((episode, src))
    db = _db(app)
    for episode, src in sources:
        item = db.get(MediaItem, episode["id"])
        item.file_path = str(src)
        item.downloaded = True
    row = db.get(Media, media["id"])
    row.jellyfin_series_id = "series-lanterns"
    db.commit()
    _cache(
        db,
        jellyfin_id="series-lanterns",
        item_type="Series",
        name="Lanternes Démo",
        year=2011,
        series_id="series-lanterns",
        tmdb_id=900102,
        date_last_saved=NOW,
    )
    report = build_report(db, app.state.settings)
    moves = report["renames"]
    assert len(moves) == 2
    assert {move["blocked"] for move in moves} == {ID_CONFLICT}
    assert all("tmdbid-900103" in move["src"] for move in moves)
    assert all("Lanternes Démo (2011) [tmdbid-900102]" in move["dest"] for move in moves)
    saved = client.patch(
        "/api/v1/settings",
        json={"values": {"jellyfin_auto_rename": True}},
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text
    jellyfin = _client(_idle([]))
    try:
        validated = apply_renames(db, app.state.settings, jellyfin, validated=True, now=NOW)
        automatic = apply_renames(
            db, app.state.settings, jellyfin, validated=False, automatic=True, now=NOW
        )
        forced = dict(moves[0])
        forced["blocked"] = None
        commit_renames(db, app.state.settings, jellyfin, [forced], NOW)
    finally:
        jellyfin.close()
    db.commit()
    assert validated["moved"] == 0
    assert automatic["moved"] == 0
    assert all(src.is_file() for _episode, src in sources)
    db.close()


def test_detective_attach_does_not_cross_another_tmdb_tag(client, app):
    login(client)
    detective_demo = _series(client, "Détective Démo", 2511, year=2025)
    episode = _episode(client, detective_demo["id"], 1, 1)
    root = Path(app.state.settings.data_dir) / "media" / "Series"
    src = root / "Détective Démo (2025) [tmdbid-2511]" / "Season 01" / "junk.mkv"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"video")
    db = _db(app)
    item = db.get(MediaItem, episode["id"])
    item.file_path = str(src)
    item.downloaded = True
    db.commit()
    _cache(
        db,
        jellyfin_id="series-detective-demo",
        item_type="Series",
        name="Détective Démo",
        year=2025,
        series_id="series-detective-demo",
        tmdb_id=2511,
    )
    report = build_report(db, app.state.settings)
    assert [row["title"] for row in report["attaches"]] == ["Détective Démo"]
    move = report["renames"][0]
    assert move["blocked"] is None
    assert "[tmdbid-2511]" in move["src"]
    assert "[tmdbid-2511]" in move["dest"]
    assert ID_CONFLICT not in {row["blocked"] for row in report["renames"]}
    db.close()


def test_failed_jellyfin_push_is_visible(client, app):
    login(client)
    media = _series(client, "Lanternes Démo", 900104, year=2026)
    db = _db(app)
    db.add(
        JellyfinPushJob(
            media_id=media["id"],
            tmdb_id=900104,
            title="Lanternes Démo",
            state="failed",
            attempts=5,
            last_error="Jellyfin a refusé la ré-identification.",
            idempotency_key="lanterns-fail",
            created_at=NOW,
        )
    )
    db.commit()
    db.close()
    listed = client.get("/api/v1/library/jellyfin")
    assert listed.status_code == 200, listed.text
    pushes = listed.json()["pushes"]
    assert len(pushes) == 1
    assert pushes[0]["state"] == "failed"
    assert pushes[0]["last_error"] == "Jellyfin a refusé la ré-identification."
