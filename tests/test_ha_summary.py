"""Home Assistant summary: one local payload, bearer scopes, and queue hold."""

from datetime import date, datetime, timedelta, timezone

from tests.conftest import csrf, login
from tests.test_pipeline import FakeDebrid, FakeDownloader, _enable_debrid, _link, _series

from mediatracker.core.pipeline import tick
from mediatracker.core.source_force import pump_forced_source_check
from mediatracker.db.models import CatalogEntry, Download, LibraryFile, Media, MediaItem, Provider, Setting


def _bearer(client, scopes: list[str], name: str) -> dict[str, str]:
    created = client.post(
        "/api/v1/tokens",
        json={"name": name, "scopes": scopes},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    return {"Authorization": f"Bearer {created.json()['token']}"}


def test_summary_shape_and_token_scopes(client, app):
    assert client.get("/api/v1/ha/summary").status_code == 401
    assert client.get("/api/v1/ha/summary", headers={"Authorization": "Bearer mt_nope"}).status_code == 401

    login(client)
    _enable_debrid(app)
    film = client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Archipel Pilote", "year": 2021},
        headers=csrf(client),
    )
    assert film.status_code == 201, film.text
    series = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Exemple", "year": 2024},
        headers=csrf(client),
    )
    assert series.status_code == 201, series.text
    now = datetime.now(timezone.utc)
    soon = date.today() + timedelta(days=2)
    db = app.state.session_factory()
    try:
        film_item = MediaItem(
            media_id=film.json()["id"],
            title="Archipel Pilote",
            downloaded=True,
            watched=False,
            downloaded_at=now,
            state="present",
        )
        episode = MediaItem(
            media_id=series.json()["id"],
            season=2,
            episode=4,
            title="Suite",
            downloaded=True,
            watched=False,
            downloaded_at=now,
            state="present",
        )
        nxt = MediaItem(
            media_id=series.json()["id"],
            season=2,
            episode=5,
            title="Demain",
            air_date=soon,
            downloaded=False,
            watched=False,
            state="missing",
        )
        db.add_all([film_item, episode, nxt])
        db.flush()
        db.add_all(
            [
                LibraryFile(
                    path="/data/films/archipel-pilote.mkv",
                    media_type="film",
                    status="matched",
                    media_item_id=film_item.id,
                    seen_at=now,
                ),
                LibraryFile(
                    path="/data/series/exemple-s02e04.mkv",
                    media_type="series",
                    status="matched",
                    media_item_id=episode.id,
                    seen_at=now,
                ),
            ]
        )
        film_item.file_path = "/data/films/archipel-pilote.mkv"
        episode.file_path = "/data/series/exemple-s02e04.mkv"
        for media_id in (film.json()["id"], series.json()["id"]):
            row = db.get(Media, media_id)
            if row is not None:
                row.followed = True
        db.add(
            Download(
                media_item_id=episode.id,
                state="downloading",
                progress=40,
                speed=2_097_152,
                size=1_000,
                created_at=now,
            )
        )
        db.add_all(
            [
                CatalogEntry(
                    dedupe_key="archipel-pilote|2021|film||",
                    title="Archipel Pilote",
                    year=2021,
                    media_type="film",
                    source="wawacity",
                    source_url="https://wawa.example/archipel-pilote",
                    sources=[{"source": "wawacity", "url": "https://wawa.example/archipel-pilote"}],
                    first_seen_at=now,
                    last_seen_at=now,
                    poster_url="https://image.tmdb.org/t/p/w342/archipel-pilote.jpg",
                    genres=[],
                ),
                CatalogEntry(
                    dedupe_key="exemple|2024|series||",
                    title="Exemple",
                    year=2024,
                    media_type="series",
                    source="wawacity",
                    source_url="https://wawa.example/exemple",
                    sources=[],
                    first_seen_at=now - timedelta(minutes=1),
                    last_seen_at=now,
                    poster_url="http://evil.example/poster.jpg",
                    genres=[],
                ),
                CatalogEntry(
                    dedupe_key="luke|2016|series|2|",
                    title="Héros Démo - - VF",
                    year=2016,
                    media_type="series",
                    season=2,
                    quality="HD",
                    language="VF",
                    source="wawacity",
                    source_url="https://wawa.example/luke",
                    sources=[],
                    first_seen_at=now + timedelta(seconds=1),
                    last_seen_at=now,
                    poster_url="https://image.tmdb.org/t/p/w342/luke.jpg",
                    genres=[],
                ),
                CatalogEntry(
                    dedupe_key="ignore|2020|film||",
                    title="Ignoré",
                    year=2020,
                    media_type="film",
                    source="wawacity",
                    source_url="https://wawa.example/ignore",
                    sources=[],
                    first_seen_at=now,
                    last_seen_at=now,
                    ignored=True,
                    genres=[],
                ),
            ]
        )
        media = db.get(Media, series.json()["id"])
        media.last_source_poll_at = now
        media.last_poll_result = "nothing"
        media.last_poll_message = "Rien de neuf."
        provider = db.query(Provider).filter(Provider.type == "alldebrid").one()
        provider.health_status = "degraded"
        provider.health_message = "Quota proche."
        db.commit()
    finally:
        db.close()

    read_headers = _bearer(client, ["read"], "ha-read")
    write_headers = _bearer(client, ["write"], "ha-write")
    client.cookies.clear()

    denied = client.post("/api/v1/ha/actions/downloads/pause", headers=read_headers)
    assert denied.status_code == 403
    assert client.post("/api/v1/ha/actions/check-sources", headers=read_headers).status_code == 403
    assert client.post("/api/v1/ha/actions/refresh-catalog", headers=read_headers).status_code == 403
    assert client.post("/api/v1/ha/actions/scan-library", headers=read_headers).status_code == 403

    summary = client.get("/api/v1/ha/summary", headers=read_headers)
    assert summary.status_code == 200, summary.text
    body = summary.json()
    assert body["downloads"]["active"] == 1
    assert body["downloads"]["progress"] == 40
    assert body["downloads"]["speed"] == 2_097_152
    assert body["downloads"]["paused"] is False
    assert body["queue"]["count"] == 0
    assert body["to_watch"]["films"] == 1
    assert body["to_watch"]["episodes"] == 0
    assert body["upcoming"][0]["label"] == "S02E05"
    assert body["upcoming"][0]["air_date"] == soon.isoformat()
    assert body["catalog"]["films"] == 1
    assert body["catalog"]["series"] == 2
    posters = {item["title"]: item["poster_url"] for item in body["catalog"]["latest"]}
    luke = next(item for item in body["catalog"]["latest"] if item["title"] == "Héros Démo")
    assert " - - " not in luke["title"]
    assert luke["version"] == "Saison 2 · HD · VF"
    assert luke["poster_url"].endswith("/luke.jpg")
    scanned_at = body["source_scan"]["at"]
    assert isinstance(scanned_at, str)
    assert "." not in scanned_at
    assert scanned_at.endswith("Z")
    assert posters["Archipel Pilote"] == "https://image.tmdb.org/t/p/w342/archipel-pilote.jpg"
    assert posters["Exemple"] is None
    assert "Ignoré" not in posters
    assert body["source_scan"]["media_title"] == "Exemple"
    assert body["source_scan"]["result"] == "nothing"
    assert body["health"]["status"] == "degraded"
    assert body["health"]["label"] == "dégradé"
    assert body["health"]["component"] == "alldebrid"
    assert isinstance(body["disk"]["free_bytes"], int)
    assert body["disk"]["free_bytes"] > 0
    assert "wawa.example" not in summary.text

    blocked = client.post("/api/v1/ha/actions/check-sources", headers=write_headers)
    assert blocked.status_code == 422
    refresh = client.post("/api/v1/ha/actions/refresh-catalog", headers=write_headers)
    assert refresh.status_code == 422

    db = app.state.session_factory()
    try:
        row = db.get(Setting, "wawacity_base_url")
        row.value = "https://wawa.example"
        db.commit()
    finally:
        db.close()
    scheduled = client.post("/api/v1/ha/actions/check-sources", headers=write_headers)
    assert scheduled.status_code == 200, scheduled.text
    assert "2" in scheduled.json()["detail"]

    scanned = client.post("/api/v1/ha/actions/scan-library", headers=write_headers)
    assert scanned.status_code == 200, scanned.text
    again = client.get("/api/v1/ha/summary", headers=write_headers)
    library_at = again.json()["library_scan_at"]
    assert isinstance(library_at, str)
    assert "." not in library_at
    assert library_at.endswith("Z")


def test_summary_hides_catalog_without_alldebrid(client, app):
    login(client)
    now = datetime.now(timezone.utc)
    db = app.state.session_factory()
    try:
        db.add_all(
            [
                CatalogEntry(
                    dedupe_key="masque|2021|film||",
                    title="Archipel Pilote",
                    year=2021,
                    media_type="film",
                    source="wawacity",
                    source_url="https://wawa.example/masque",
                    sources=[],
                    first_seen_at=now,
                    last_seen_at=now,
                    genres=[],
                ),
                CatalogEntry(
                    dedupe_key="masque|2024|series|1|",
                    title="Exemple Série",
                    year=2024,
                    media_type="series",
                    season=1,
                    source="wawacity",
                    source_url="https://wawa.example/masque-s",
                    sources=[],
                    first_seen_at=now,
                    last_seen_at=now,
                    genres=[],
                ),
            ]
        )
        db.commit()
    finally:
        db.close()

    headers = _bearer(client, ["read"], "ha-no-ad-catalog")
    client.cookies.clear()
    summary = client.get("/api/v1/ha/summary", headers=headers)
    assert summary.status_code == 200, summary.text
    catalog = summary.json()["catalog"]
    assert catalog["films"] == 0
    assert catalog["series"] == 0
    assert catalog["latest"] == []


def test_pause_holds_the_queue_until_resume(client, app):
    login(client)
    _enable_debrid(app)
    media = _series(client)
    link = _link(client, media["id"], "https://rapidgator.net/file/abc", 1)
    headers = _bearer(client, ["write"], "ha-hold")
    client.cookies.clear()

    paused = client.post("/api/v1/ha/actions/downloads/pause", headers=headers)
    assert paused.status_code == 200, paused.text
    debrid = FakeDebrid()
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, debrid=debrid, downloader=FakeDownloader())
        row = db.get(Download, link["download_id"])
        assert row.state == "pending"
        assert row.error_code == "USER_PAUSED"
    finally:
        db.close()
    assert debrid.seen == []
    board = client.get("/api/v1/ha/summary", headers=headers)
    assert board.json()["downloads"]["user_paused"] is True
    assert board.json()["downloads"]["pause_reason"] == "user"
    listed = client.get("/api/v1/downloads", headers=headers)
    assert listed.json()["queue_paused"] is True

    resumed = client.post("/api/v1/ha/actions/downloads/resume", headers=headers)
    assert resumed.status_code == 200, resumed.text
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, debrid=debrid, downloader=FakeDownloader())
        row = db.get(Download, link["download_id"])
        assert row.state == "queued"
    finally:
        db.close()
    assert debrid.seen == ["https://rapidgator.net/file/abc"]


def test_forced_source_check_polls_one_fiche_per_tick(client, app, monkeypatch):
    login(client)
    first = _series(client)
    second = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Autre", "year": 2023},
        headers=csrf(client),
    )
    assert second.status_code == 201, second.text
    db = app.state.session_factory()
    try:
        row = db.get(Setting, "wawacity_base_url")
        row.value = "https://wawa.example"
        held = db.get(Media, second.json()["id"])
        held.on_hold = True
        db.commit()
    finally:
        db.close()

    calls: list[int] = []

    def fake_orchestrate(_factory, _settings, media_id, *, force=False, **_kwargs):
        assert force is True
        calls.append(media_id)
        return None

    monkeypatch.setattr(
        "mediatracker.core.source_force.orchestrate_profile_first_poll",
        fake_orchestrate,
    )

    headers = _bearer(client, ["write"], "ha-check")
    client.cookies.clear()
    scheduled = client.post("/api/v1/ha/actions/check-sources", headers=headers)
    assert scheduled.status_code == 200, scheduled.text
    assert scheduled.json()["detail"].startswith("1 ")

    db = app.state.session_factory()
    try:
        assert pump_forced_source_check(db, app.state.settings) is True
        assert pump_forced_source_check(db, app.state.settings) is False
    finally:
        db.close()
    assert calls == [first["id"]]
