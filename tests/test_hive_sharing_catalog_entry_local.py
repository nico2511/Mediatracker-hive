"""Contrat catalog_entry_local : annonce locale, sans preuve pair."""

from __future__ import annotations

from tests.conftest import csrf, login
from tests.test_hive_catalog_sharing_worker import NOW, _enable_hive, _join_ruche, _origin_id
from tests.test_hive_t2 import _hash_until_done

from mediatracker.db.models import LibraryFile, Media, MediaItem
from mediatracker.hive.catalog import catalog_entry_local_for_item, sync_hive_catalog_item

pytest_plugins = ["tests.test_hive_t1"]


def _film_with_file(db, app, settings, *, tmdb_id: int, write_hash: bool = False):
    from tests.conftest import PLAUSIBLE_VIDEO_BYTES, write_sparse_video_file

    from mediatracker.core.storage import storage_layout

    layout = storage_layout(app.state.session_factory(), settings)
    video = layout.movies_dir / f"f-{tmdb_id}.mkv"
    write_sparse_video_file(video, PLAUSIBLE_VIDEO_BYTES)
    media = Media(type="film", title="Fiction", year=2099, tmdb_id=tmdb_id)
    db.add(media)
    db.flush()
    item = MediaItem(media_id=media.id, downloaded=True, file_path=str(video))
    db.add(item)
    db.flush()
    db.add(
        LibraryFile(
            path=str(video),
            size=PLAUSIBLE_VIDEO_BYTES,
            parsed_title="Fiction",
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
    item_id = item.id
    if write_hash:
        _hash_until_done(db, settings, item_id)
        item = db.get(MediaItem, item_id)
    return media, item


def test_sharing_api_includes_catalog_entry_local_false_before_hash(client, app, settings):
    _enable_hive(client)
    origin = _origin_id(settings)
    ruche_id = bytes([0xC1] * 15 + [1])
    db = app.state.session_factory()
    try:
        _join_ruche(db, origin, ruche_id, settings)
        _, item = _film_with_file(db, app, settings, tmdb_id=88001, write_hash=False)
        item_id = item.id
        db.commit()
    finally:
        db.close()
    patch = client.patch(
        f"/api/v1/hive/items/{item_id}/sharing",
        json={"exclude_all": False, "excluded_ruche_ids": []},
        headers=csrf(client),
    )
    assert patch.status_code == 204, patch.text
    body = client.get(f"/api/v1/hive/items/{item_id}/sharing").json()
    assert body["catalog_entry_local"] is False
    assert body["shareable"] is False


def test_catalog_entry_local_true_after_hash_and_sync(client, app, settings):
    _enable_hive(client)
    origin = _origin_id(settings)
    ruche_id = bytes([0xC2] * 15 + [2])
    db = app.state.session_factory()
    try:
        _join_ruche(db, origin, ruche_id, settings)
        _, item = _film_with_file(db, app, settings, tmdb_id=88002, write_hash=True)
        sync_hive_catalog_item(db, settings, item.id, now=NOW)
        db.commit()
        assert catalog_entry_local_for_item(db, settings, item) is True
        item_id = item.id
    finally:
        db.close()
    body = client.get(f"/api/v1/hive/items/{item_id}/sharing").json()
    assert body["catalog_entry_local"] is True
    assert body["shareable"] is True


def test_catalog_entry_local_false_when_exclude_all(client, app, settings):
    _enable_hive(client)
    origin = _origin_id(settings)
    ruche_id = bytes([0xC3] * 15 + [3])
    db = app.state.session_factory()
    try:
        _join_ruche(db, origin, ruche_id, settings)
        _, item = _film_with_file(db, app, settings, tmdb_id=88003, write_hash=True)
        sync_hive_catalog_item(db, settings, item.id, now=NOW)
        db.commit()
        item_id = item.id
    finally:
        db.close()
    assert (
        client.patch(
            f"/api/v1/hive/items/{item_id}/sharing",
            json={"exclude_all": True, "excluded_ruche_ids": []},
            headers=csrf(client),
        ).status_code
        == 204
    )
    body = client.get(f"/api/v1/hive/items/{item_id}/sharing").json()
    assert body["catalog_entry_local"] is False
    assert body["exclude_all"] is True


def test_patch_204_before_sha_never_catalog_entry_local(client, app, settings):
    """PATCH avec intention de partage sans empreinte : pas d'annonce locale exploitable."""
    login(client)
    _enable_hive(client)
    db = app.state.session_factory()
    try:
        media = Media(type="film", title="SansFichier", year=2099, tmdb_id=88004)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, state="missing")
        db.add(item)
        db.commit()
        item_id = item.id
    finally:
        db.close()
    assert (
        client.patch(
            f"/api/v1/hive/items/{item_id}/sharing",
            json={"exclude_all": False, "excluded_ruche_ids": []},
            headers=csrf(client),
        ).status_code
        == 204
    )
    body = client.get(f"/api/v1/hive/items/{item_id}/sharing").json()
    assert body["catalog_entry_local"] is False
    assert body["has_file"] is False


def test_catalog_entry_local_requires_all_member_ruches(client, app, settings):
    """Deux ruches membres : une exclusion partielle → pas d'annonce complète."""
    from mediatracker.hive.share_exclusions import replace_share_exclusions

    _enable_hive(client)
    origin = _origin_id(settings)
    ruche_a = bytes([0xD1] * 15 + [1])
    ruche_b = bytes([0xD2] * 15 + [2])
    db = app.state.session_factory()
    try:
        _join_ruche(db, origin, ruche_a, settings)
        _join_ruche(db, origin, ruche_b, settings)
        _, item = _film_with_file(db, app, settings, tmdb_id=88005, write_hash=True)
        sync_hive_catalog_item(db, settings, item.id, now=NOW)
        assert catalog_entry_local_for_item(db, settings, item) is True
        replace_share_exclusions(db, item.id, exclude_all=False, ruche_ids=[ruche_b])
        sync_hive_catalog_item(db, settings, item.id, now=NOW)
        db.commit()
        assert catalog_entry_local_for_item(db, settings, item) is True
        replace_share_exclusions(db, item.id, exclude_all=False, ruche_ids=[ruche_a, ruche_b])
        sync_hive_catalog_item(db, settings, item.id, now=NOW)
        db.commit()
        assert catalog_entry_local_for_item(db, settings, item) is False
        item_id = item.id
    finally:
        db.close()
    body = client.get(f"/api/v1/hive/items/{item_id}/sharing").json()
    assert body["catalog_entry_local"] is False
    assert len(body["excluded_ruche_ids"]) == 2


def test_catalog_entry_local_false_during_rehash_with_tombstone(client, app, settings):
    """Après tombstone (exclude_all), jamais d'annonce locale même si empreinte revient."""
    from mediatracker.db.models import HiveFileFingerprint
    from mediatracker.hive.share_exclusions import replace_share_exclusions

    _enable_hive(client)
    origin = _origin_id(settings)
    ruche_id = bytes([0xD3] * 15 + [3])
    db = app.state.session_factory()
    try:
        _join_ruche(db, origin, ruche_id, settings)
        _, item = _film_with_file(db, app, settings, tmdb_id=88006, write_hash=True)
        sync_hive_catalog_item(db, settings, item.id, now=NOW)
        replace_share_exclusions(db, item.id, exclude_all=True, ruche_ids=[])
        sync_hive_catalog_item(db, settings, item.id, now=NOW)
        fp = db.get(HiveFileFingerprint, item.id)
        assert fp is not None
        fp.sha256_hex = "f" * 64
        sync_hive_catalog_item(db, settings, item.id, now=NOW)
        db.commit()
        item_id = item.id
        assert catalog_entry_local_for_item(db, settings, item) is False
    finally:
        db.close()
    body = client.get(f"/api/v1/hive/items/{item_id}/sharing").json()
    assert body["catalog_entry_local"] is False
    assert body["exclude_all"] is True
