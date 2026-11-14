"""Accueil regroupé : champs supplémentaires du tableau de bord."""

from datetime import date, datetime, timedelta, timezone

from tests.conftest import connect_alldebrid_for_tests, csrf, login

from mediatracker.db.models import CatalogEntry, DigitalRelease, LibraryFile, Media, MediaItem
from mediatracker.events import record_library_imported


def test_dashboard_catalog_hidden_without_usable_latest_source(client, app):
    login(client)
    now = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        db.add(
            CatalogEntry(
                dedupe_key="stale-film|2025|film||",
                title="Film Figé",
                year=2025,
                media_type="film",
                quality="HD",
                language="VFF",
                source="wawacity",
                source_url="https://example.invalid/stale-film",
                sources=[],
                first_seen_at=now,
                last_seen_at=now,
                title_folded="film fige",
            )
        )
        db.add(
            CatalogEntry(
                dedupe_key="stale-series|2025|series||",
                title="Série Figée",
                year=2025,
                media_type="series",
                quality="HD",
                language="VFF",
                source="wawacity",
                source_url="https://example.invalid/stale-series",
                sources=[],
                first_seen_at=now,
                last_seen_at=now,
                title_folded="serie figee",
            )
        )
        db.commit()
    finally:
        db.close()
    payload = client.get("/api/v1/dashboard").json()
    assert payload["catalog_films"] == []
    assert payload["catalog_series"] == []
    assert payload["latest"] == []


def test_dashboard_includes_home_grid_sections(client, app):
    login(client)
    connect_alldebrid_for_tests(app)
    now = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
    created = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Archipel Démo", "year": 2024},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    media_id = created.json()["id"]
    item = client.post(
        f"/api/v1/media/{media_id}/items",
        json={"season": 1, "episode": 1, "title": "Pilote", "downloaded": True},
        headers=csrf(client),
    )
    assert item.status_code == 201, item.text
    db = app.state.session_factory()
    try:
        row = db.get(MediaItem, item.json()["id"])
        row.downloaded_at = now
        row.file_path = "/srv/series/archipel-pilote.mkv"
        db.add(
            LibraryFile(
                path=row.file_path,
                media_type="series",
                status="matched",
                media_item_id=row.id,
                seen_at=now,
            )
        )
        media_row = db.get(Media, media_id)
        record_library_imported(db, media_row, row, row.file_path)
        db.add(
            CatalogEntry(
                dedupe_key="film-demo|2025|film||",
                title="Film Démo",
                year=2025,
                media_type="film",
                quality="HD",
                language="VFF",
                source="wawacity",
                source_url="https://example.invalid/film-demo",
                sources=[],
                first_seen_at=now,
                last_seen_at=now,
                title_folded="film demo",
            )
        )
        db.add(
            CatalogEntry(
                dedupe_key="serie-demo|2025|series||",
                title="Série Démo",
                year=2025,
                media_type="series",
                quality="HD",
                language="VFF",
                source="wawacity",
                source_url="https://example.invalid/serie-demo",
                sources=[],
                first_seen_at=now,
                last_seen_at=now,
                title_folded="serie demo",
            )
        )
        db.add(
            DigitalRelease(
                tmdb_id=901002,
                media_type="film",
                title="Sortie Démo",
                release_on=date.today() + timedelta(days=5),
                refreshed_at=now,
            )
        )
        db.commit()
    finally:
        db.close()

    board = client.get("/api/v1/dashboard")
    assert board.status_code == 200, board.text
    payload = board.json()
    assert "recent_downloads" in payload
    assert payload["recent_downloads"][0]["media_title"] == "Archipel Démo"
    assert payload["recent_downloads"][0]["downloaded_at"]
    assert any(card["title"] == "Sortie Démo" for card in payload["upcoming_releases"])
    assert any(card["title"] == "Film Démo" for card in payload["catalog_films"])
    assert any(card["title"] == "Série Démo" for card in payload["catalog_series"])
    followed = client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Archipel Pilote", "year": 2021, "tmdb_id": 11},
        headers=csrf(client),
    )
    assert followed.status_code == 201, followed.text
    db = app.state.session_factory()
    try:
        media = db.get(Media, followed.json()["id"])
        media.followed = True
        db.add(
            DigitalRelease(
                tmdb_id=11,
                media_type="film",
                title="Archipel Pilote",
                release_on=date.today() + timedelta(days=3),
                refreshed_at=now,
            )
        )
        db.commit()
    finally:
        db.close()
    board2 = client.get("/api/v1/dashboard").json()
    assert not any(card["tmdb_id"] == 11 for card in board2["upcoming_releases"])
    assert any(card["tmdb_id"] == 901002 for card in board2["upcoming_releases"])


def test_dashboard_upcoming_week_includes_still_url(client, app):
    login(client)
    created = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Horizon Test", "year": 2024},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    media_id = created.json()["id"]
    soon = (date.today() + timedelta(days=2)).isoformat()
    item = client.post(
        f"/api/v1/media/{media_id}/items",
        json={"season": 1, "episode": 2, "title": "Épisode test", "air_date": soon},
        headers=csrf(client),
    )
    assert item.status_code == 201, item.text
    db = app.state.session_factory()
    try:
        row = db.get(MediaItem, item.json()["id"])
        row.still_url = "https://image.tmdb.org/t/p/w780/still-demo.jpg"
        db.commit()
    finally:
        db.close()

    board = client.get("/api/v1/dashboard").json()
    match = next(
        card
        for card in board["upcoming_week"]
        if card["media_id"] == media_id and card["season"] == 1 and card["episode"] == 2
    )
    assert match["still_url"] == "https://image.tmdb.org/t/p/w780/still-demo.jpg"
