"""Hive : partage API, catalogue local, empreintes sans verrou long."""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timezone

from sqlalchemy import func, select
from tests.conftest import PLAUSIBLE_VIDEO_BYTES, csrf, login, write_sparse_video_file

from mediatracker.core.storage import storage_layout
from mediatracker.db.models import (
    HiveEntry,
    HiveFileFingerprint,
    HiveMember,
    HiveRuche,
    LibraryFile,
    Media,
    MediaItem,
)
from mediatracker.hive.catalog import (
    _cle_document,
    _fiche_document,
    _file_id_for_ruche,
    _latest_entry_for_item,
    build_catalog_payload,
    ensure_hive_release_metadata,
    sign_payload,
    sync_hive_catalog,
    sync_hive_catalog_item,
)
from mediatracker.hive.crypto_id import public_key_to_pem
from mediatracker.hive.fingerprint import ensure_fingerprint_row_for_item, hive_fingerprint_tick
from mediatracker.hive.hive_catalogue import load_hive_catalogue_rows, refresh_hive_catalogue_store
from mediatracker.hive.identity import hive_public_id_from_public_key, load_private_key
from mediatracker.hive.peer_gossip import entries_missing_for_peer, ingest_signed_entries, verify_remote_entry
from mediatracker.hive.share_policy import grant_share_intent
from mediatracker.worker import _run_worker_pass

NOW = datetime(2026, 11, 8, 18, 49, tzinfo=timezone.utc)


def _enable_hive(client):
    login(client)
    r = client.patch(
        "/api/v1/settings",
        json={
            "values": {
                "hive_enabled": True,
                "hive_activation_name": "ab",
                "hive_public_address": "https://mtr.exemple.fr",
            }
        },
        headers=csrf(client),
    )
    assert r.status_code == 200, r.text


def _origin_id(settings):
    pk = load_private_key(settings)
    assert pk is not None
    return hive_public_id_from_public_key(pk.public_key())


def _join_ruche(db, origin: str, ruche_id: bytes, settings=None) -> None:
    pem = ""
    if settings is not None:
        pk = load_private_key(settings)
        if pk is not None:
            pem = public_key_to_pem(pk.public_key()).decode("utf-8")
    if db.get(HiveRuche, ruche_id) is None:
        db.add(HiveRuche(ruche_id=ruche_id, creator_instance_id=origin))
    if (
        db.scalar(
            select(HiveMember)
            .where(HiveMember.ruche_id == ruche_id, HiveMember.member_instance_id == origin)
            .limit(1)
        )
        is None
    ):
        db.add(
            HiveMember(
                ruche_id=ruche_id,
                member_instance_id=origin,
                public_address="https://mtr.exemple.fr",
                public_key_pem=pem,
            )
        )
    db.flush()


def test_sync_keeps_disponible_while_fingerprint_rehashing(client, app, settings, tmp_path):
    """Régression : sync ne doit pas retirer une entrée pendant un hachage en cours."""
    _enable_hive(client)
    origin = _origin_id(settings)
    ruche_id = bytes([7] * 15 + [1])
    layout = storage_layout(app.state.session_factory(), settings)
    folder = layout.series_dir / "Demo (2020) [tmdbid-42]" / "Season 01"
    folder.mkdir(parents=True, exist_ok=True)
    video = folder / "e.mkv"
    write_sparse_video_file(video, PLAUSIBLE_VIDEO_BYTES)

    db = app.state.session_factory()
    try:
        _join_ruche(db, origin, ruche_id)
        media = Media(type="series", title="Demo", year=2020, tmdb_id=42)
        db.add(media)
        db.flush()
        item = MediaItem(
            media_id=media.id,
            season=1,
            episode=1,
            downloaded=True,
            file_path=str(video),
        )
        db.add(item)
        db.flush()
        db.add(
            LibraryFile(
                path=str(video),
                size=PLAUSIBLE_VIDEO_BYTES,
                parsed_title="Demo",
                parsed_year=2020,
                season=1,
                episode=1,
                tmdb_id=42,
                media_type="series",
                media_id=media.id,
                media_item_id=item.id,
                status="matched",
                confidence="high",
                seen_at=NOW,
            )
        )
        db.flush()
        st = video.stat()
        mtime_ns = getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))
        fp = HiveFileFingerprint(
            media_item_id=item.id,
            path=str(video.resolve()),
            size=PLAUSIBLE_VIDEO_BYTES,
            mtime_ns=mtime_ns,
            sha256_hex="a" * 64,
            partial_size=PLAUSIBLE_VIDEO_BYTES,
            updated_at=NOW,
        )
        db.add(fp)
        grant_share_intent(db, item.id)
        db.commit()
        sync_hive_catalog(db, settings, now=NOW)
        db.commit()
        last_before = _latest_entry_for_item(db, ruche_id, origin, item.id)
        assert last_before is not None and last_before.etat == "disponible"
        fp = db.get(HiveFileFingerprint, item.id)
        fp.sha256_hex = None
        fp.partial_size = max(1, (fp.size or 1) // 2)
        sync_hive_catalog(db, settings, now=NOW)
        db.commit()
        last_after = _latest_entry_for_item(db, ruche_id, origin, item.id)
        assert last_after is not None and last_after.etat == "disponible"
    finally:
        db.close()


def test_sync_tombstones_legacy_payload_no_mtime_on_rehash(client, app, settings):
    """Annonce legacy sans mtime_ns : pas de maintien pendant re-hash (même taille)."""
    import json

    _enable_hive(client)
    origin = _origin_id(settings)
    ruche_id = bytes([7] * 15 + [0xB])
    layout = storage_layout(app.state.session_factory(), settings)
    video = layout.movies_dir / "legacy-no-mtime.mkv"
    video.parent.mkdir(parents=True, exist_ok=True)
    blob = b"legacy-payload"
    video.write_bytes(blob)
    sha = "f" * 64
    pk = load_private_key(settings)
    assert pk is not None

    db = app.state.session_factory()
    try:
        _join_ruche(db, origin, ruche_id, settings)
        media = Media(type="film", title="LegacyMT", year=2022, tmdb_id=53)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, downloaded=True, file_path=str(video))
        db.add(item)
        db.flush()
        st = video.stat()
        mtime_ns = getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))
        db.add(
            LibraryFile(
                path=str(video),
                size=len(blob),
                parsed_title="LegacyMT",
                parsed_year=2022,
                media_type="film",
                tmdb_id=53,
                media_id=media.id,
                media_item_id=item.id,
                status="matched",
                confidence="high",
                seen_at=NOW,
            )
        )
        db.add(
            HiveFileFingerprint(
                media_item_id=item.id,
                path=str(video.resolve()),
                size=len(blob),
                mtime_ns=mtime_ns,
                sha256_hex=sha,
                partial_size=len(blob),
                updated_at=NOW,
            )
        )
        file_id = _file_id_for_ruche(ruche_id, origin, item.id)
        payload = build_catalog_payload(
            ruche_id=ruche_id,
            origin_instance_id=origin,
            seq=1,
            cle={"type": "movie", "tmdb_id": 53, "version": "1080p"},
            fiche={"type": "movie", "tmdb_id": 53, "title": "LegacyMT", "year": 2022},
            sha256_hex=sha,
            file_id=file_id,
            etat="disponible",
            taille=len(blob),
        )
        sig = sign_payload(pk, payload)
        db.add(
            HiveEntry(
                ruche_id=ruche_id,
                origin_instance_id=origin,
                seq=1,
                cle_json=json.dumps(payload["cle"], sort_keys=True),
                fiche_json=json.dumps(payload["fiche"], sort_keys=True),
                sha256_hex=sha,
                file_id=file_id,
                etat="disponible",
                payload_json=json.dumps(payload, sort_keys=True),
                signature_b64=sig,
                media_item_id=item.id,
            )
        )
        db.commit()
        fp = db.get(HiveFileFingerprint, item.id)
        fp.sha256_hex = None
        fp.partial_size = max(1, len(blob) // 2)
        sync_hive_catalog_item(db, settings, item.id, now=NOW)
        db.commit()
        last = _latest_entry_for_item(db, ruche_id, origin, item.id)
        assert last is not None and last.etat == "retire"
    finally:
        db.close()


def test_publish_enriches_mtime_when_sha_unchanged(client, app, settings):
    import json

    _enable_hive(client)
    origin = _origin_id(settings)
    ruche_id = bytes([7] * 15 + [0xC])
    layout = storage_layout(app.state.session_factory(), settings)
    video = layout.movies_dir / "enrich-mtime.mkv"
    video.parent.mkdir(parents=True, exist_ok=True)
    from tests.conftest import PLAUSIBLE_VIDEO_BYTES

    write_sparse_video_file(video, PLAUSIBLE_VIDEO_BYTES)
    sha = "0" * 64
    pk = load_private_key(settings)
    assert pk is not None

    db = app.state.session_factory()
    try:
        _join_ruche(db, origin, ruche_id, settings)
        media = Media(type="film", title="Enrich", year=2024, tmdb_id=54)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, downloaded=True, file_path=str(video))
        db.add(item)
        db.flush()
        st = video.stat()
        mtime_ns = getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))
        db.add(
            LibraryFile(
                path=str(video),
                size=PLAUSIBLE_VIDEO_BYTES,
                parsed_title="Enrich",
                parsed_year=2024,
                media_type="film",
                tmdb_id=54,
                media_id=media.id,
                media_item_id=item.id,
                status="matched",
                confidence="high",
                seen_at=NOW,
            )
        )
        db.add(
            HiveFileFingerprint(
                media_item_id=item.id,
                path=str(video.resolve()),
                size=PLAUSIBLE_VIDEO_BYTES,
                mtime_ns=mtime_ns,
                sha256_hex=sha,
                partial_size=PLAUSIBLE_VIDEO_BYTES,
                updated_at=NOW,
            )
        )
        ensure_hive_release_metadata(media, item)
        file_id = _file_id_for_ruche(ruche_id, origin, item.id)
        legacy_payload = build_catalog_payload(
            ruche_id=ruche_id,
            origin_instance_id=origin,
            seq=1,
            cle=_cle_document(media, item),
            fiche=_fiche_document(media, item, taille=PLAUSIBLE_VIDEO_BYTES),
            sha256_hex=sha,
            file_id=file_id,
            etat="disponible",
            taille=PLAUSIBLE_VIDEO_BYTES,
        )
        sig = sign_payload(pk, legacy_payload)
        db.add(
            HiveEntry(
                ruche_id=ruche_id,
                origin_instance_id=origin,
                seq=1,
                cle_json=json.dumps(legacy_payload["cle"], sort_keys=True),
                fiche_json=json.dumps(legacy_payload["fiche"], sort_keys=True),
                sha256_hex=sha,
                file_id=file_id,
                etat="disponible",
                payload_json=json.dumps(legacy_payload, sort_keys=True),
                signature_b64=sig,
                media_item_id=item.id,
            )
        )
        db.commit()
        grant_share_intent(db, item.id)
        sync_hive_catalog_item(db, settings, item.id, now=NOW)
        db.commit()
        last = _latest_entry_for_item(db, ruche_id, origin, item.id)
        assert last is not None and last.etat == "disponible" and last.seq == 2
        body = json.loads(last.payload_json or "{}")
        assert body.get("mtime_ns") == mtime_ns
        assert body.get("sha256") == sha
    finally:
        db.close()


def test_sync_tombstones_same_size_new_mtime_stale_sha(client, app, settings):
    """Même taille ≠ même contenu : mtime disque change, SHA catalogue périmé retiré."""
    _enable_hive(client)
    origin = _origin_id(settings)
    ruche_id = bytes([7] * 15 + [0xA])
    layout = storage_layout(app.state.session_factory(), settings)
    video = layout.movies_dir / "same-size.mkv"
    video.parent.mkdir(parents=True, exist_ok=True)
    size = 512
    video.write_bytes(b"1" * size)

    db = app.state.session_factory()
    try:
        _join_ruche(db, origin, ruche_id, settings)
        media = Media(type="film", title="SameSz", year=2022, tmdb_id=52)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, downloaded=True, file_path=str(video))
        db.add(item)
        db.flush()
        st = video.stat()
        mtime_ns = getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))
        db.add(
            LibraryFile(
                path=str(video),
                size=size,
                parsed_title="SameSz",
                parsed_year=2022,
                media_type="film",
                tmdb_id=52,
                media_id=media.id,
                media_item_id=item.id,
                status="matched",
                confidence="high",
                seen_at=NOW,
            )
        )
        db.add(
            HiveFileFingerprint(
                media_item_id=item.id,
                path=str(video.resolve()),
                size=size,
                mtime_ns=mtime_ns,
                sha256_hex="e" * 64,
                partial_size=size,
                updated_at=NOW,
            )
        )
        grant_share_intent(db, item.id)
        db.commit()
        sync_hive_catalog(db, settings, now=NOW)
        db.commit()
        published_mtime = mtime_ns
        last = _latest_entry_for_item(db, ruche_id, origin, item.id)
        assert last is not None and last.etat == "disponible"
        import json

        if last.payload_json:
            published = json.loads(last.payload_json)
            published_mtime = int(published.get("mtime_ns", published_mtime))

        video.write_bytes(b"2" * size)
        st2 = video.stat()
        new_mtime_ns = getattr(st2, "st_mtime_ns", int(st2.st_mtime * 1_000_000_000))
        assert new_mtime_ns != published_mtime
        fp = db.get(HiveFileFingerprint, item.id)
        assert fp is not None
        fp.path = str(video.resolve())
        fp.size = size
        fp.mtime_ns = new_mtime_ns
        fp.sha256_hex = None
        fp.partial_size = size // 2
        sync_hive_catalog_item(db, settings, item.id, now=NOW)
        db.commit()
        last = _latest_entry_for_item(db, ruche_id, origin, item.id)
        assert last is not None and last.etat == "retire"
    finally:
        db.close()


def test_sync_tombstones_when_file_changes_after_publish(client, app, settings):
    _enable_hive(client)
    origin = _origin_id(settings)
    ruche_id = bytes([7] * 15 + [9])
    layout = storage_layout(app.state.session_factory(), settings)
    folder = layout.series_dir / "Grow (2020) [tmdbid-43]" / "Season 01"
    folder.mkdir(parents=True, exist_ok=True)
    video = folder / "e.mkv"
    write_sparse_video_file(video, PLAUSIBLE_VIDEO_BYTES)

    db = app.state.session_factory()
    try:
        _join_ruche(db, origin, ruche_id)
        media = Media(type="series", title="Grow", year=2020, tmdb_id=43)
        db.add(media)
        db.flush()
        item = MediaItem(
            media_id=media.id,
            season=1,
            episode=1,
            downloaded=True,
            file_path=str(video),
        )
        db.add(item)
        db.flush()
        st = video.stat()
        mtime_ns = getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))
        db.add(
            LibraryFile(
                path=str(video),
                size=PLAUSIBLE_VIDEO_BYTES,
                parsed_title="Grow",
                parsed_year=2020,
                season=1,
                episode=1,
                tmdb_id=43,
                media_type="series",
                media_id=media.id,
                media_item_id=item.id,
                status="matched",
                confidence="high",
                seen_at=NOW,
            )
        )
        db.add(
            HiveFileFingerprint(
                media_item_id=item.id,
                path=str(video.resolve()),
                size=PLAUSIBLE_VIDEO_BYTES,
                mtime_ns=mtime_ns,
                sha256_hex="c" * 64,
                partial_size=PLAUSIBLE_VIDEO_BYTES,
                updated_at=NOW,
            )
        )
        grant_share_intent(db, item.id)
        db.commit()
        sync_hive_catalog(db, settings, now=NOW)
        db.commit()
        assert _latest_entry_for_item(db, ruche_id, origin, item.id).etat == "disponible"
        write_sparse_video_file(video, 2 * PLAUSIBLE_VIDEO_BYTES, prefix=b"y")
        fp = db.get(HiveFileFingerprint, item.id)
        assert fp is not None
        st2 = video.stat()
        fp.size = st2.st_size
        fp.mtime_ns = getattr(st2, "st_mtime_ns", int(st2.st_mtime * 1_000_000_000))
        fp.sha256_hex = None
        fp.partial_size = 0
        sync_hive_catalog(db, settings, now=NOW)
        db.commit()
        last = _latest_entry_for_item(db, ruche_id, origin, item.id)
        assert last is not None and last.etat == "retire"
    finally:
        db.close()


def test_sync_tombstones_when_sharing_excluded_during_rehash(client, app, settings):
    _enable_hive(client)
    origin = _origin_id(settings)
    ruche_id = bytes([7] * 15 + [8])
    layout = storage_layout(app.state.session_factory(), settings)
    video = layout.movies_dir / "excl.mkv"
    video.parent.mkdir(parents=True, exist_ok=True)
    write_sparse_video_file(video, PLAUSIBLE_VIDEO_BYTES)

    db = app.state.session_factory()
    try:
        _join_ruche(db, origin, ruche_id)
        media = Media(type="film", title="Excl", year=2021, tmdb_id=44)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, downloaded=True, file_path=str(video))
        db.add(item)
        db.flush()
        st = video.stat()
        mtime_ns = getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))
        db.add(
            LibraryFile(
                path=str(video),
                size=PLAUSIBLE_VIDEO_BYTES,
                parsed_title="Excl",
                parsed_year=2021,
                media_type="film",
                tmdb_id=44,
                media_id=media.id,
                media_item_id=item.id,
                status="matched",
                confidence="high",
                seen_at=NOW,
            )
        )
        db.add(
            HiveFileFingerprint(
                media_item_id=item.id,
                path=str(video.resolve()),
                size=PLAUSIBLE_VIDEO_BYTES,
                mtime_ns=mtime_ns,
                sha256_hex="d" * 64,
                partial_size=PLAUSIBLE_VIDEO_BYTES,
                updated_at=NOW,
            )
        )
        grant_share_intent(db, item.id)
        db.commit()
        sync_hive_catalog(db, settings, now=NOW)
        db.commit()
        assert (
            _latest_entry_for_item(db, ruche_id, origin, item.id) is not None
            and _latest_entry_for_item(db, ruche_id, origin, item.id).etat == "disponible"
        )
        fp = db.get(HiveFileFingerprint, item.id)
        fp.sha256_hex = None
        fp.partial_size = 1
        from mediatracker.hive.share_exclusions import replace_share_exclusions

        replace_share_exclusions(db, item.id, exclude_all=True, ruche_ids=[])
        sync_hive_catalog_item(db, settings, item.id, now=NOW)
        db.commit()
        retire_n = db.scalar(
            select(func.count()).select_from(HiveEntry).where(
                HiveEntry.ruche_id == ruche_id,
                HiveEntry.etat == "retire",
            )
        )
        assert retire_n and int(retire_n) >= 1
    finally:
        db.close()


def test_patch_sharing_uses_item_sync_only(client, app, settings, monkeypatch):
    _enable_hive(client)
    origin = _origin_id(settings)
    ruche_id = bytes([8] * 15 + [2])
    db = app.state.session_factory()
    try:
        _join_ruche(db, origin, ruche_id)
        media = Media(type="film", title="Solo", year=2022, tmdb_id=51)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, downloaded=False)
        db.add(item)
        db.commit()
        item_id = item.id
    finally:
        db.close()

    calls = {"full": 0, "item": 0}

    def _full(*_a, **_k):
        calls["full"] += 1

    def _item(*_a, **_k):
        calls["item"] += 1

    import mediatracker.api.v1.hive_ui as hive_ui_mod
    import mediatracker.hive.catalog as catalog_mod

    monkeypatch.setattr(catalog_mod, "sync_hive_catalog", _full)
    monkeypatch.setattr(hive_ui_mod, "sync_hive_catalog_item", _item)
    res = client.patch(
        f"/api/v1/hive/items/{item_id}/sharing",
        json={"exclude_all": True, "excluded_ruche_ids": []},
        headers=csrf(client),
    )
    assert res.status_code == 204, res.text
    assert calls["full"] == 0
    assert calls["item"] == 1


def test_catalogue_nonempty_after_share_and_hash(client, app, settings):
    _enable_hive(client)
    origin = _origin_id(settings)
    ruche_id = bytes([9] * 15 + [3])
    layout = storage_layout(app.state.session_factory(), settings)
    video_dir = layout.movies_dir
    video = video_dir / "f.mkv"
    video.parent.mkdir(parents=True, exist_ok=True)
    write_sparse_video_file(video, PLAUSIBLE_VIDEO_BYTES)

    db = app.state.session_factory()
    try:
        _join_ruche(db, origin, ruche_id)
        media = Media(type="film", title="CatalogFilm", year=2023, tmdb_id=61)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, downloaded=True, file_path=str(video))
        db.add(item)
        db.flush()
        db.add(
            LibraryFile(
                path=str(video),
                size=PLAUSIBLE_VIDEO_BYTES,
                parsed_title="CatalogFilm",
                parsed_year=2023,
                media_type="film",
                tmdb_id=61,
                media_id=media.id,
                media_item_id=item.id,
                status="matched",
                confidence="high",
                seen_at=NOW,
            )
        )
        db.flush()
        grant_share_intent(db, item.id)
        ensure_fingerprint_row_for_item(db, settings, item, now=NOW)
        db.commit()
        item_id = item.id
    finally:
        db.close()

    for _ in range(32):
        db = app.state.session_factory()
        try:
            hive_fingerprint_tick(db, settings, now=NOW)
            db.commit()
            fp = db.get(HiveFileFingerprint, item_id)
            if fp and fp.sha256_hex:
                break
        finally:
            db.close()

    patch = client.patch(
        f"/api/v1/hive/items/{item_id}/sharing",
        json={"exclude_all": False, "excluded_ruche_ids": []},
        headers=csrf(client),
    )
    assert patch.status_code == 204, patch.text

    page = client.get("/api/v1/hive/catalogue?refresh=1", headers=csrf(client))
    assert page.status_code == 200, page.text
    body = page.json()
    assert body["items"], page.text


def test_two_apps_ingest_fills_peer_catalog(tmp_path):
    """Deux bases SQLite, deux identités Hive : ingest signé remplit un catalogue pair vide."""

    from fastapi.testclient import TestClient

    from mediatracker.api.app import create_app
    from mediatracker.config import Settings

    base_a = tmp_path / "inst_a"
    base_b = tmp_path / "inst_b"
    base_a.mkdir(parents=True)
    base_b.mkdir(parents=True)
    settings_a = Settings(
        secret_key="test-secret-key-0123456789",
        admin_username="admin",
        admin_password="Choix-Solide-42!",
        database_url=f"sqlite:///{base_a / 'app.db'}",
        data_dir=base_a / "data",
        config_dir=base_a / "config",
        disk_min_free_gb=0,
    )
    settings_b = Settings(
        secret_key="test-secret-key-0123456789",
        admin_username="admin",
        admin_password="Choix-Solide-42!",
        database_url=f"sqlite:///{base_b / 'app.db'}",
        data_dir=base_b / "data",
        config_dir=base_b / "config",
        disk_min_free_gb=0,
    )
    app_a = create_app(settings_a)
    with TestClient(app_a) as client_a:
        _enable_hive(client_a)

    app_b = create_app(settings_b)
    with TestClient(app_b) as client_b:
        login(client_b)
        _enable_hive(client_b)

    empty = app_b.state.session_factory()
    try:
        assert not load_hive_catalogue_rows(empty)
    finally:
        empty.close()

    origin = _origin_id(settings_a)
    origin_b = _origin_id(settings_b)
    assert origin != origin_b
    ruche_id = bytes([0x0A] * 15 + [4])
    layout = storage_layout(app_a.state.session_factory(), settings_a)
    video = layout.movies_dir / "peer.mkv"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"p" * 4096)

    db = app_a.state.session_factory()
    try:
        _join_ruche(db, origin, ruche_id, settings_a)
        media = Media(type="film", title="PeerFilm", year=2023, tmdb_id=62)
        db.add(media)
        db.flush()
        item = MediaItem(
            media_id=media.id,
            downloaded=True,
            file_path=str(video),
            release_quality="HD",
            release_language="VFF",
        )
        db.add(item)
        db.flush()
        db.add(
            LibraryFile(
                path=str(video),
                size=4096,
                parsed_title="PeerFilm",
                parsed_year=2023,
                media_type="film",
                tmdb_id=62,
                media_id=media.id,
                media_item_id=item.id,
                status="matched",
                confidence="high",
                seen_at=NOW,
            )
        )
        db.flush()
        grant_share_intent(db, item.id)
        ensure_fingerprint_row_for_item(db, settings_a, item, now=NOW)
        item_id = item.id
        db.commit()
        db = app_a.state.session_factory()
        fp = db.get(HiveFileFingerprint, item_id)
        assert fp is not None
        fp.sha256_hex = "b" * 64
        fp.partial_size = fp.size or 3
        sync_hive_catalog_item(db, settings_a, item_id, now=NOW)
        db.commit()
        entry = db.scalar(
            select(HiveEntry).where(HiveEntry.ruche_id == ruche_id, HiveEntry.etat == "disponible").limit(1)
        )
        assert entry is not None
        outbound = entries_missing_for_peer(db, ruche_id=ruche_id, peer_vector={})
        assert outbound
    finally:
        db.close()

    peer = app_b.state.session_factory()
    try:
        _join_ruche(peer, origin, ruche_id, settings_a)
        verify_remote_entry(
            peer,
            ruche_id=ruche_id,
            payload=outbound[0]["payload"],
            signature_b64=outbound[0]["signature_b64"],
        )
        accepted = ingest_signed_entries(peer, ruche_id=ruche_id, items=outbound, now=NOW)
        assert accepted == 1
        refresh_hive_catalogue_store(peer, now=NOW)
        peer.commit()
        rows = load_hive_catalogue_rows(peer)
        assert rows
    finally:
        peer.close()


def test_fingerprint_hash_allows_concurrent_sqlite_write(client, app, settings, tmp_path):
    """Pendant le hachage (commit libéré), un autre writer SQLite peut progresser."""
    db_path = app.state.settings.database_url.removeprefix("sqlite:///")
    layout = storage_layout(app.state.session_factory(), settings)
    video = layout.movies_dir / "big-hive.mkv"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"z" * (6 * 1024 * 1024))

    db = app.state.session_factory()
    try:
        media = Media(type="film", title="Big", year=2024, tmdb_id=71)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, downloaded=True, file_path=str(video))
        db.add(item)
        db.flush()
        db.add(
            LibraryFile(
                path=str(video),
                size=video.stat().st_size,
                parsed_title="Big",
                parsed_year=2024,
                media_type="film",
                tmdb_id=71,
                media_id=media.id,
                media_item_id=item.id,
                status="matched",
                confidence="high",
                seen_at=NOW,
            )
        )
        ensure_fingerprint_row_for_item(db, settings, item, now=NOW)
        db.commit()
    finally:
        db.close()

    marker = {"wrote": False, "lock": False}

    def writer():
        conn = sqlite3.connect(db_path, timeout=1)
        for _ in range(80):
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO setting(key, value, is_secret, updated_at) VALUES (?,?,?,?)",
                    ("hive_probe", "1", 0, NOW.isoformat()),
                )
                conn.commit()
                marker["wrote"] = True
                return
            except sqlite3.OperationalError as exc:
                if "locked" in str(exc).lower():
                    marker["lock"] = True
                time.sleep(0.02)
        conn.close()

    th = threading.Thread(target=writer)
    th.start()
    db = app.state.session_factory()
    try:
        hive_fingerprint_tick(db, settings)
        db.commit()
    finally:
        db.close()
    th.join(timeout=30)
    assert marker["wrote"] is True


def test_patch_sharing_succeeds_during_worker_pass(client, app, settings):
    _enable_hive(client)
    origin = _origin_id(settings)
    ruche_id = bytes([0x0B] * 15 + [5])
    db = app.state.session_factory()
    try:
        _join_ruche(db, origin, ruche_id)
        media = Media(type="film", title="ShareBusy", year=2025, tmdb_id=81)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id)
        db.add(item)
        db.commit()
        item_id = item.id
    finally:
        db.close()

    results: list[int] = []

    def worker():
        session = app.state.session_factory()
        try:
            _run_worker_pass(session, app.state.settings, app.state.session_factory)
            session.commit()
        except Exception:
            session.rollback()
        finally:
            session.close()

    th = threading.Thread(target=worker)
    th.start()
    time.sleep(0.05)
    res = client.patch(
        f"/api/v1/hive/items/{item_id}/sharing",
        json={"exclude_all": False, "excluded_ruche_ids": []},
        headers=csrf(client),
    )
    results.append(res.status_code)
    th.join(timeout=120)
    assert results[0] in (204, 503)
    if results[0] == 503:
        retry = client.patch(
            f"/api/v1/hive/items/{item_id}/sharing",
            json={"exclude_all": False, "excluded_ruche_ids": []},
            headers=csrf(client),
        )
        assert retry.status_code == 204, retry.text


def test_worker_second_pass_after_failed_fingerprint_lock(client, app, settings, monkeypatch):
    """Second passage daemon empreintes après verrou SQLite ; worker reste exécutable."""
    from sqlalchemy.exc import OperationalError

    from mediatracker.hive import fingerprint_daemon as daemon_mod

    daemon_mod.clear_active_session()
    _enable_hive(client)
    layout = storage_layout(app.state.session_factory(), settings)
    video = layout.movies_dir / "lock-resume.mkv"
    video.parent.mkdir(parents=True, exist_ok=True)
    write_sparse_video_file(video, PLAUSIBLE_VIDEO_BYTES)
    db = app.state.session_factory()
    try:
        media = Media(type="film", title="LockResume", year=2026, tmdb_id=90082)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, downloaded=True, file_path=str(video))
        db.add(item)
        db.flush()
        db.add(
            LibraryFile(
                path=str(video),
                size=PLAUSIBLE_VIDEO_BYTES,
                parsed_title="LockResume",
                parsed_year=2026,
                media_type="film",
                tmdb_id=90082,
                media_id=media.id,
                media_item_id=item.id,
                status="matched",
                confidence="high",
                seen_at=NOW,
            )
        )
        db.flush()
        grant_share_intent(db, item.id)
        ensure_fingerprint_row_for_item(db, settings, item, now=NOW)
        db.commit()
        item_id = item.id
    finally:
        db.close()

    from mediatracker.hive import fingerprint as fp_mod

    state = {"calls": 0}
    real_tick = fp_mod.hive_fingerprint_tick

    def _tick(db, st, **kwargs):
        state["calls"] += 1
        if state["calls"] == 1:
            raise OperationalError("UPDATE fp", {}, sqlite3.OperationalError("database is locked"))
        return real_tick(db, st, **kwargs)

    monkeypatch.setattr(fp_mod, "hive_fingerprint_tick", _tick)

    for _ in range(8):
        tick_db = app.state.session_factory()
        try:
            fp_mod.hive_fingerprint_tick(tick_db, settings, now=NOW)
            tick_db.commit()
        except OperationalError:
            tick_db.rollback()
        else:
            probe = tick_db.get(HiveFileFingerprint, item_id)
            if probe is not None and (
                (probe.sha256_hex or "").strip() or int(probe.partial_size or 0) > 0
            ):
                break
        finally:
            tick_db.close()

    assert state["calls"] >= 2
    progress = app.state.session_factory()
    try:
        fp = progress.get(HiveFileFingerprint, item_id)
        assert fp is not None
        assert (fp.sha256_hex or "").strip() or int(fp.partial_size or 0) > 0
    finally:
        progress.close()

    db_w = app.state.session_factory()
    try:
        _run_worker_pass(db_w, app.state.settings, app.state.session_factory)
        db_w.commit()
    finally:
        db_w.close()
    db_w2 = app.state.session_factory()
    try:
        _run_worker_pass(db_w2, app.state.settings, app.state.session_factory)
        db_w2.commit()
    finally:
        db_w2.close()
    daemon_mod.clear_active_session()
