"""Masquage réversible des fiches média."""

from datetime import datetime, timezone

from tests.conftest import csrf, login
from tests.test_hive_download_cherche_retire import (
    NOW,
    _enable_hive,
    _join_fixture_ruche,
    _latest_signal_row,
    _origin_id,
)

from mediatracker.db.models import CatalogEntry, LibraryFile, Media, MediaItem
from mediatracker.hive.catalog import _cle_document
from mediatracker.hive.signal import sync_possede_for_item


def _film(client, *, title: str, tmdb_id: int) -> int:
    created = client.post(
        "/api/v1/media",
        json={"type": "film", "title": title, "year": 2098, "tmdb_id": tmdb_id},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def test_hide_unhide_roundtrip(client, app):
    login(client)
    media_id = _film(client, title="Exemple masquage", tmdb_id=880001)
    hidden = client.post(f"/api/v1/media/{media_id}/hide", headers=csrf(client))
    assert hidden.status_code == 200, hidden.text
    assert hidden.json()["hidden_at"] is not None

    listing = client.get("/api/v1/media", params={"limit": 50}).json()
    assert all(row["id"] != media_id for row in listing["items"])
    assert listing["hidden_count"] >= 1

    with_hidden = client.get("/api/v1/media", params={"limit": 50, "include_hidden": "true"}).json()
    assert any(row["id"] == media_id for row in with_hidden["items"])
    assert with_hidden["hidden_count"] >= 1

    shown = client.post(f"/api/v1/media/{media_id}/unhide", headers=csrf(client))
    assert shown.status_code == 200, shown.text
    assert shown.json()["hidden_at"] is None
    listing_after = client.get("/api/v1/media", params={"limit": 50}).json()
    assert any(row["id"] == media_id for row in listing_after["items"])


def test_hidden_fiche_absent_from_dashboard_to_watch(client, app):
    login(client)
    media_id = _film(client, title="Exemple hors accueil", tmdb_id=880002)
    item = client.post(
        f"/api/v1/media/{media_id}/items",
        json={"downloaded": True},
        headers=csrf(client),
    )
    assert item.status_code == 201, item.text
    now = datetime.now(timezone.utc)
    db = app.state.session_factory()
    try:
        row = db.get(MediaItem, item.json()["id"])
        row.file_path = "/srv/exemple-hors-accueil.mkv"
        db.add(
            LibraryFile(
                path=row.file_path,
                media_type="film",
                status="matched",
                media_item_id=row.id,
                seen_at=now,
            )
        )
        db.commit()
    finally:
        db.close()
    before = client.get("/api/v1/dashboard").json()
    assert any(card["media_id"] == media_id for card in before["to_watch"])

    client.post(f"/api/v1/media/{media_id}/hide", headers=csrf(client))
    after = client.get("/api/v1/dashboard").json()
    assert all(card["media_id"] != media_id for card in after["to_watch"])


def test_fiche_index_keeps_hidden_link_catalog_list_omits(client, app):
    login(client)
    tmdb_id = 880003
    media_id = _film(client, title="Exemple catalogue masqué", tmdb_id=tmdb_id)
    now = datetime.now(timezone.utc)
    db = app.state.session_factory()
    try:
        from mediatracker.api.v1.catalog import _fiche_index
        from mediatracker.core.catalog import fold_title

        entry = CatalogEntry(
            dedupe_key=f"film:{tmdb_id}:hide-demo",
            title="Exemple catalogue masqué",
            title_folded=fold_title("Exemple catalogue masqué"),
            year=2098,
            media_type="film",
            source="wawacity",
            source_url="https://wawa.test/film/hide-demo",
            sources=[],
            tmdb_id=tmdb_id,
            first_seen_at=now,
            last_seen_at=now,
        )
        db.add(entry)
        db.commit()
        entry_id = entry.id

        by_tmdb, _by_title = _fiche_index(db)
        assert by_tmdb[("film", tmdb_id)] == media_id

        media = db.get(Media, media_id)
        media.hidden_at = datetime.now(timezone.utc)
        db.commit()

        by_tmdb_after, _ = _fiche_index(db)
        assert by_tmdb_after[("film", tmdb_id)] == media_id
    finally:
        db.close()

    visible = client.get("/api/v1/catalog", params={"type": "film"}).json()
    assert not any(row["id"] == entry_id for row in visible["items"])


def test_hide_unfollows_followed_fiche(client, app):
    login(client)
    media_id = _film(client, title="Exemple suivi masqué", tmdb_id=880006)
    hidden = client.post(f"/api/v1/media/{media_id}/hide", headers=csrf(client))
    assert hidden.status_code == 200, hidden.text
    body = hidden.json()
    assert body["hidden_at"] is not None
    assert body["followed"] is False


def test_follow_clears_hidden(client, app):
    login(client)
    media_id = _film(client, title="Exemple re-suivi", tmdb_id=880007)
    client.post(f"/api/v1/media/{media_id}/hide", headers=csrf(client))
    followed = client.post(f"/api/v1/media/{media_id}/follow", json={}, headers=csrf(client))
    assert followed.status_code == 200, followed.text
    body = followed.json()
    assert body["hidden_at"] is None
    assert body["followed"] is True
    listing = client.get("/api/v1/media", params={"limit": 50}).json()
    assert any(row["id"] == media_id for row in listing["items"])


def test_possede_published_for_hidden_unfollowed_film_with_file(client, settings, tmp_path):
    _enable_hive(client)
    login(client)
    db = client.app.state.session_factory()
    try:
        origin = _origin_id(settings)
        rid = _join_fixture_ruche(db, origin)
        media = Media(
            type="film",
            title="Exemple possede masque",
            year=2099,
            tmdb_id=880004,
            followed=False,
            hidden_at=NOW,
        )
        db.add(media)
        db.flush()
        fpath = tmp_path / "owned-hidden.mkv"
        fpath.write_bytes(b"data")
        item = MediaItem(
            media_id=media.id,
            downloaded=True,
            file_path=str(fpath),
            release_quality="1080p",
            release_language="fr",
        )
        db.add(item)
        db.flush()
        cle = _cle_document(media, item)
        sync_possede_for_item(db, settings, media=media, item=item, now=NOW)
        db.flush()
        latest = _latest_signal_row(db, ruche_id=rid, origin_id=origin, cle=cle)
        assert latest is not None and latest.signal == "possede"
        db.commit()
    finally:
        db.close()