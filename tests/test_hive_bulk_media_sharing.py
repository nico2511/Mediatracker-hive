"""Partage Hive groupé sur plusieurs fiches média (une requête)."""

from __future__ import annotations

from datetime import datetime, timezone

from tests.conftest import PLAUSIBLE_VIDEO_BYTES, csrf, write_sparse_video_file

from mediatracker.core.storage import storage_layout
from mediatracker.db.models import LibraryFile, Media, MediaItem

pytest_plugins = ["tests.test_hive_t1"]

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)


def _enable_hive(client):
    from tests.test_hive_t2 import _enable_hive as base

    base(client)


def _origin_join(client, app, settings):
    from tests.test_hive_catalog_sharing_worker import _join_ruche, _origin_id

    origin = _origin_id(settings)
    ruche_id = bytes([0xB2] * 15 + [2])
    db = app.state.session_factory()
    try:
        _join_ruche(db, origin, ruche_id, settings)
        db.commit()
    finally:
        db.close()
    return ruche_id


def _film_on_disk(db, app, settings, *, tmdb_id: int, title: str) -> tuple[Media, MediaItem]:
    layout = storage_layout(app.state.session_factory(), settings)
    folder = layout.movies_dir / f"{title} (2099) [tmdbid-{tmdb_id}]"
    folder.mkdir(parents=True, exist_ok=True)
    video = folder / "film.mkv"
    write_sparse_video_file(video, PLAUSIBLE_VIDEO_BYTES, prefix=b"bk")
    media = Media(type="film", title=title, year=2099, tmdb_id=tmdb_id)
    db.add(media)
    db.flush()
    item = MediaItem(
        media_id=media.id,
        title=title,
        state="downloaded",
        downloaded=True,
        file_path=str(video),
    )
    db.add(item)
    db.flush()
    db.add(
        LibraryFile(
            path=str(video),
            size=PLAUSIBLE_VIDEO_BYTES,
            parsed_title=title,
            parsed_year=2099,
            media_type="film",
            tmdb_id=tmdb_id,
            media_id=media.id,
            media_item_id=item.id,
            status="matched",
            confidence="high",
            seen_at=NOW,
        )
    )
    db.commit()
    return media, item


def test_bulk_share_counts_ignored_and_shared(client, app, settings):
    _enable_hive(client)
    _origin_join(client, app, settings)
    db = app.state.session_factory()
    try:
        media_ok, item = _film_on_disk(db, app, settings, tmdb_id=92001, title="Fiche fictive A")
        media_empty = Media(type="film", title="Fiche vide", year=2099, tmdb_id=92002)
        db.add(media_empty)
        db.commit()
        id_ok = int(media_ok.id)
        id_empty = int(media_empty.id)
        item_id = int(item.id)
    finally:
        db.close()

    r = client.patch(
        "/api/v1/hive/media/bulk-sharing",
        json={"media_ids": [id_empty, id_ok, 999_999], "exclude_all": False, "excluded_ruche_ids": []},
        headers=csrf(client),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["acted_count"] == 1
    assert body["ignored_no_file_count"] == 1
    assert body["ignored_pull_only_count"] == 0
    assert body["failed_count"] == 1
    assert body["exclude_all"] is False

    shared = client.get(f"/api/v1/hive/items/{item_id}/sharing").json()
    assert shared["exclude_all"] is False


def test_bulk_unshare_without_files_isolation(client, app, settings):
    _enable_hive(client)
    _origin_join(client, app, settings)
    db = app.state.session_factory()
    try:
        media_ok, item = _film_on_disk(db, app, settings, tmdb_id=92011, title="Fiche fictive B")
        media_empty = Media(type="film", title="Fiche vide B", year=2099, tmdb_id=92012)
        db.add(media_empty)
        db.commit()
        id_ok = int(media_ok.id)
        id_empty = int(media_empty.id)
        item_id = int(item.id)
    finally:
        db.close()

    client.patch(
        f"/api/v1/hive/media/{id_ok}/sharing",
        json={"exclude_all": False, "excluded_ruche_ids": []},
        headers=csrf(client),
    ).raise_for_status()

    r = client.patch(
        "/api/v1/hive/media/bulk-sharing",
        json={"media_ids": [id_ok, id_empty], "exclude_all": True, "excluded_ruche_ids": []},
        headers=csrf(client),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["acted_count"] == 1
    assert body["ignored_no_file_count"] == 1
    assert body["ignored_pull_only_count"] == 0
    assert body["failed_count"] == 0

    state = client.get(f"/api/v1/hive/items/{item_id}/sharing").json()
    assert state["exclude_all"] is True

def test_bulk_share_rejected_when_hive_off(client, app, settings):
    from tests.conftest import login

    login(client)
    r = client.patch(
        "/api/v1/hive/media/bulk-sharing",
        json={"media_ids": [1], "exclude_all": False, "excluded_ruche_ids": []},
        headers=csrf(client),
    )
    assert r.status_code == 404
    assert "Hive est désactivé" in r.text


def test_bulk_share_rejects_more_than_200_ids(client, app, settings):
    _enable_hive(client)
    r = client.patch(
        "/api/v1/hive/media/bulk-sharing",
        json={"media_ids": list(range(1, 202)), "exclude_all": False, "excluded_ruche_ids": []},
        headers=csrf(client),
    )
    assert r.status_code == 422


def test_bulk_share_pull_only_is_ignored_not_failed(client, app, settings):
    _enable_hive(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"hive_public_address": ""}},
        headers=csrf(client),
    ).raise_for_status()
    db = app.state.session_factory()
    try:
        media_ok, item = _film_on_disk(db, app, settings, tmdb_id=92021, title="Fiche tirage")
        media_empty = Media(type="film", title="Fiche vide tirage", year=2099, tmdb_id=92022)
        db.add(media_empty)
        db.commit()
        id_ok = int(media_ok.id)
        id_empty = int(media_empty.id)
        item_id = int(item.id)
    finally:
        db.close()

    r = client.patch(
        "/api/v1/hive/media/bulk-sharing",
        json={"media_ids": [id_ok, id_empty], "exclude_all": False, "excluded_ruche_ids": []},
        headers=csrf(client),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["acted_count"] == 0
    assert body["ignored_no_file_count"] == 1
    assert body["ignored_pull_only_count"] == 1
    assert body["failed_count"] == 0
    by_id = {row["media_id"]: row for row in body["results"]}
    assert by_id[id_ok]["status"] == "ignored_pull_only"
    assert "tirage" in (by_id[id_ok]["message"] or "").lower()
    assert by_id[id_empty]["status"] == "ignored_no_file"

    # Nothing was granted despite the on-disk fiche.
    missing = client.get(f"/api/v1/hive/items/{item_id}/sharing")
    assert missing.status_code in (200, 404)
    if missing.status_code == 200:
        assert missing.json()["exclude_all"] is True
