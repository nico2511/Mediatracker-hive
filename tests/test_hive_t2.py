"""Hive T2 : partageable, empreintes, entrées signées, exclusions, Garder Hive."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidSignature
from sqlalchemy import func, select, update
from tests.conftest import catalog_min_logical_video_bytes, csrf, login, write_sparse_video_file

from mediatracker.core.storage import storage_layout
from mediatracker.db.models import (
    HiveEntry,
    HiveFileFingerprint,
    HiveMember,
    HiveRuche,
    HiveShareExclusion,
    LibraryFile,
    Media,
    MediaItem,
    TrashEntry,
)
from mediatracker.hive.catalog import (
    build_catalog_payload,
    item_is_shareable_base,
    purge_old_tombstones,
    sign_payload,
    sync_hive_catalog,
    verify_catalog_payload,
)
from mediatracker.hive.fingerprint import ensure_fingerprint_row_for_item, hive_fingerprint_tick
from mediatracker.hive.identity import hive_public_id_from_public_key, load_private_key
from mediatracker.hive.share_policy import grant_share_intent

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)


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


# Id de ruche arbitraire pour les fixtures (jamais créé par sync_hive_catalog).
_TEST_FIXTURE_RUCHE_ID = bytes([0] * 15 + [1])


def _join_fixture_ruche(db, origin_instance_id: str, ruche_id: bytes | None = None) -> bytes:
    rid = ruche_id or _TEST_FIXTURE_RUCHE_ID
    if db.get(HiveRuche, rid) is None:
        db.add(HiveRuche(ruche_id=rid, creator_instance_id=origin_instance_id))
    if (
        db.scalar(
            select(HiveMember)
            .where(HiveMember.ruche_id == rid, HiveMember.member_instance_id == origin_instance_id)
            .limit(1)
        )
        is None
    ):
        db.add(HiveMember(ruche_id=rid, member_instance_id=origin_instance_id))
    return rid


def _write_publishable_video(video: Path, *, prefix: bytes = b"x") -> int:
    logical = catalog_min_logical_video_bytes()
    write_sparse_video_file(video, logical, prefix=prefix)
    return logical


def _hash_until_done(db, settings, item_id: int) -> None:
    item = db.get(MediaItem, item_id)
    assert item is not None
    grant_share_intent(db, item_id)
    ensure_fingerprint_row_for_item(db, settings, item, now=NOW)
    db.commit()
    for _ in range(500):
        hive_fingerprint_tick(db, settings)
        db.commit()
        fp = db.get(HiveFileFingerprint, item_id)
        if fp and fp.sha256_hex:
            return
    raise AssertionError("empreinte SHA-256 non terminée")


def test_absent_or_trash_not_shareable(client, app, settings):
    _enable_hive(client)
    layout = storage_layout(app.state.session_factory(), settings)
    folder = layout.series_dir / "Show (2000) [tmdbid-1]" / "Season 01"
    folder.mkdir(parents=True, exist_ok=True)
    video = folder / "e.mkv"
    video.write_bytes(b"v")

    db = app.state.session_factory()
    try:
        media = Media(type="series", title="Show", year=2000, tmdb_id=1)
        db.add(media)
        db.flush()
        item = MediaItem(
            media_id=media.id,
            season=1,
            episode=1,
            title="E1",
            state="downloaded",
            downloaded=True,
            file_path=str(video),
        )
        db.add(item)
        db.flush()
        assert item_is_shareable_base(db, item, hive_enabled=True) is False
        db.add(
            LibraryFile(
                path=str(video),
                size=1,
                parsed_title="Show",
                parsed_year=2000,
                season=1,
                episode=1,
                tmdb_id=1,
                media_type="series",
                media_id=media.id,
                media_item_id=item.id,
                status="matched",
                confidence="high",
                seen_at=NOW,
            )
        )
        db.commit()
        item_id = item.id
    finally:
        db.close()

    db = app.state.session_factory()
    try:
        item = db.get(MediaItem, item_id)
        _hash_until_done(db, settings, item_id)
        db.commit()
        item = db.get(MediaItem, item_id)
        assert item_is_shareable_base(db, item, hive_enabled=True) is True
        lf = db.scalar(select(LibraryFile).where(LibraryFile.media_item_id == item_id).limit(1))
        assert lf is not None
        lf.status = "missing"
        lf.missing_at = NOW
        db.execute(update(MediaItem).where(MediaItem.id == item_id).values(file_path=None))
        db.commit()
        item = db.get(MediaItem, item_id)
        ensure_fingerprint_row_for_item(db, settings, item, now=NOW)
        db.commit()
        item = db.get(MediaItem, item_id)
        assert item_is_shareable_base(db, item, hive_enabled=True) is False
    finally:
        db.close()

    db = app.state.session_factory()
    try:
        item = db.get(MediaItem, item_id)
        item.file_path = str(video)
        lf2 = db.scalar(select(LibraryFile).where(LibraryFile.media_item_id == item_id).limit(1))
        assert lf2 is not None
        lf2.path = str(video)
        lf2.status = "trashed"
        lf2.missing_at = None
        db.commit()
        item = db.get(MediaItem, item_id)
        assert item_is_shareable_base(db, item, hive_enabled=True) is False
    finally:
        db.close()


def test_verify_rejects_tampered_byte(client, app):
    _enable_hive(client)
    settings = app.state.settings
    pk = load_private_key(settings)
    assert pk is not None
    ruche_id = _TEST_FIXTURE_RUCHE_ID
    payload = build_catalog_payload(
        ruche_id=ruche_id,
        origin_instance_id="hv1test",
        seq=1,
        cle={"type": "film"},
        fiche={"type": "film"},
        sha256_hex="a" * 64,
        file_id=b"\x01" * 16,
        etat="disponible",
    )
    sig = sign_payload(pk, payload)
    payload2 = dict(payload)
    payload2["sha256"] = "b" + "a" * 63
    with pytest.raises(InvalidSignature):
        verify_catalog_payload(settings, expected_ruche_id=ruche_id, payload=payload2, signature_b64=sig)


def test_verify_rejects_ruche_id_mismatch(client, app):
    _enable_hive(client)
    settings = app.state.settings
    pk = load_private_key(settings)
    assert pk is not None
    ruche_a = bytes([1] + [0] * 15)
    ruche_b = bytes([2] + [0] * 15)
    payload = build_catalog_payload(
        ruche_id=ruche_a,
        origin_instance_id=hive_public_id_from_public_key(pk.public_key()),
        seq=1,
        cle={"type": "film"},
        fiche={"type": "film"},
        sha256_hex="a" * 64,
        file_id=b"\x03" * 16,
        etat="disponible",
    )
    sig = sign_payload(pk, payload)
    with pytest.raises(ValueError, match="bad_ruche"):
        verify_catalog_payload(settings, expected_ruche_id=ruche_b, payload=payload, signature_b64=sig)


def test_sync_does_not_create_ruche_without_membership(client, app, settings):
    _enable_hive(client)
    db = app.state.session_factory()
    try:
        n_r = db.scalar(select(func.count()).select_from(HiveRuche)) or 0
        n_m = db.scalar(select(func.count()).select_from(HiveMember)) or 0
        sync_hive_catalog(db, settings, now=NOW)
        db.commit()
        assert (db.scalar(select(func.count()).select_from(HiveRuche)) or 0) == n_r == 0
        assert (db.scalar(select(func.count()).select_from(HiveMember)) or 0) == n_m == 0
        assert (db.scalar(select(func.count()).select_from(HiveEntry)) or 0) == 0
    finally:
        db.close()


def test_repartage_other_ruche_has_no_trace_of_first(client, app, settings):
    _enable_hive(client)
    origin = _origin_id(settings)
    ruche_a = bytes([3] + [0] * 15)
    ruche_b = bytes([4] + [0] * 15)
    db = app.state.session_factory()
    try:
        for rid in (ruche_a, ruche_b):
            if db.get(HiveRuche, rid) is None:
                db.add(HiveRuche(ruche_id=rid, creator_instance_id=origin))
            db.merge(HiveMember(ruche_id=rid, member_instance_id=origin))
        db.commit()
    finally:
        db.close()

    layout = storage_layout(app.state.session_factory(), settings)
    folder = layout.series_dir / "Other (2001) [tmdbid-2]" / "Season 01"
    folder.mkdir(parents=True, exist_ok=True)
    video = folder / "e.mkv"
    repartage_size = _write_publishable_video(video, prefix=b"c")

    db = app.state.session_factory()
    try:
        media = Media(type="series", title="Other", year=2001, tmdb_id=2)
        db.add(media)
        db.flush()
        item = MediaItem(
            media_id=media.id,
            season=1,
            episode=1,
            state="downloaded",
            downloaded=True,
            file_path=str(video),
        )
        db.add(item)
        db.flush()
        db.add(
            LibraryFile(
                path=str(video),
                size=repartage_size,
                parsed_title="Other",
                parsed_year=2001,
                season=1,
                episode=1,
                tmdb_id=2,
                media_type="series",
                media_id=media.id,
                media_item_id=item.id,
                status="matched",
                confidence="high",
                seen_at=NOW,
            )
        )
        db.commit()
        item_id = item.id
    finally:
        db.close()

    db = app.state.session_factory()
    try:
        _hash_until_done(db, settings, item_id)
        sync_hive_catalog(db, settings, now=NOW)
        db.commit()
        rows_b = list(
            db.scalars(
                select(HiveEntry).where(HiveEntry.ruche_id == ruche_b, HiveEntry.etat == "disponible")
            ).all()
        )
        assert rows_b
        raw = rows_b[-1].payload_json
        assert raw is not None
        assert ruche_a.hex() not in raw
        assert "Other" in raw
    finally:
        db.close()


def test_exclusion_single_ruche_only(client, app, settings):
    _enable_hive(client)
    origin = _origin_id(settings)
    ruche_a = bytes([5] + [0] * 15)
    ruche_b = bytes([6] + [0] * 15)

    layout = storage_layout(app.state.session_factory(), settings)
    folder = layout.series_dir / "Excl (2002) [tmdbid-3]" / "Season 01"
    folder.mkdir(parents=True, exist_ok=True)
    video = folder / "e.mkv"
    excl_size = _write_publishable_video(video, prefix=b"x")

    db = app.state.session_factory()
    try:
        for rid in (ruche_a, ruche_b):
            db.merge(HiveRuche(ruche_id=rid, creator_instance_id=origin))
            db.merge(HiveMember(ruche_id=rid, member_instance_id=origin))
        media = Media(type="series", title="Excl", year=2002, tmdb_id=3)
        db.add(media)
        db.flush()
        item = MediaItem(
            media_id=media.id,
            season=1,
            episode=1,
            state="downloaded",
            downloaded=True,
            file_path=str(video),
        )
        db.add(item)
        db.flush()
        db.add(
            LibraryFile(
                path=str(video),
                size=excl_size,
                parsed_title="Excl",
                parsed_year=2002,
                season=1,
                episode=1,
                tmdb_id=3,
                media_type="series",
                media_id=media.id,
                media_item_id=item.id,
                status="matched",
                confidence="high",
                seen_at=NOW,
            )
        )
        db.add(HiveShareExclusion(media_item_id=item.id, ruche_id=ruche_b))
        db.commit()
        item_id = item.id
    finally:
        db.close()

    db = app.state.session_factory()
    try:
        _hash_until_done(db, settings, item_id)
        sync_hive_catalog(db, settings, now=NOW)
        db.commit()
        a_row = db.scalar(
            select(HiveEntry).where(
                HiveEntry.ruche_id == ruche_a,
                HiveEntry.media_item_id == item_id,
                HiveEntry.etat == "disponible",
            ).limit(1)
        )
        b_row = db.scalar(
            select(HiveEntry).where(
                HiveEntry.ruche_id == ruche_b,
                HiveEntry.media_item_id == item_id,
                HiveEntry.etat == "disponible",
            ).limit(1)
        )
        assert a_row is not None
        assert b_row is None
    finally:
        db.close()


def test_hive_off_clears_only_hive_keep(client, app, settings):
    _enable_hive(client)
    db = app.state.session_factory()
    try:
        media = Media(type="series", title="K", year=2003, tmdb_id=4)
        db.add(media)
        db.flush()
        item_keep = MediaItem(
            media_id=media.id,
            season=1,
            episode=1,
            title="K",
            state="downloaded",
            downloaded=True,
            keep=True,
            keep_source="hive",
        )
        item_manual = MediaItem(
            media_id=media.id,
            season=1,
            episode=2,
            title="M",
            state="downloaded",
            downloaded=True,
            keep=True,
            keep_source=None,
        )
        db.add(item_keep)
        db.add(item_manual)
        db.commit()
    finally:
        db.close()

    login(client)
    r = client.patch(
        "/api/v1/settings",
        json={"values": {"hive_enabled": False}},
        headers=csrf(client),
    )
    assert r.status_code == 200, r.text

    db = app.state.session_factory()
    try:
        kept = list(db.scalars(select(MediaItem).where(MediaItem.keep.is_(True))).all())
        assert len(kept) == 1
        assert kept[0].title == "M"
    finally:
        db.close()


def test_seq_monotonic_and_file_id_differs_per_ruche(client, app, settings):
    _enable_hive(client)
    origin = _origin_id(settings)
    ra = bytes([7] + [0] * 15)
    rb = bytes([8] + [0] * 15)
    layout = storage_layout(app.state.session_factory(), settings)
    folder = layout.series_dir / "Seq (2004) [tmdbid-5]" / "Season 01"
    folder.mkdir(parents=True, exist_ok=True)
    video = folder / "e.mkv"
    seq_size = _write_publishable_video(video, prefix=b"s")

    db = app.state.session_factory()
    try:
        for rid in (ra, rb):
            db.merge(HiveRuche(ruche_id=rid, creator_instance_id=origin))
            db.merge(HiveMember(ruche_id=rid, member_instance_id=origin))
        media = Media(type="series", title="Seq", year=2004, tmdb_id=5)
        db.add(media)
        db.flush()
        item = MediaItem(
            media_id=media.id,
            season=1,
            episode=1,
            state="downloaded",
            downloaded=True,
            file_path=str(video),
        )
        db.add(item)
        db.flush()
        db.add(
            LibraryFile(
                path=str(video),
                size=seq_size,
                parsed_title="Seq",
                parsed_year=2004,
                season=1,
                episode=1,
                tmdb_id=5,
                media_type="series",
                media_id=media.id,
                media_item_id=item.id,
                status="matched",
                confidence="high",
                seen_at=NOW,
            )
        )
        db.commit()
        item_id = item.id
    finally:
        db.close()

    db = app.state.session_factory()
    try:
        _hash_until_done(db, settings, item_id)
        sync_hive_catalog(db, settings, now=NOW)
        db.commit()
        ea = list(db.scalars(select(HiveEntry).where(HiveEntry.ruche_id == ra, HiveEntry.etat == "disponible")).all())
        eb = list(db.scalars(select(HiveEntry).where(HiveEntry.ruche_id == rb, HiveEntry.etat == "disponible")).all())
        assert len(ea) == 1 and len(eb) == 1
        assert ea[0].seq >= 1 and eb[0].seq >= 1
        assert ea[0].file_id != eb[0].file_id
    finally:
        db.close()


def test_purge_old_tombstones_after_30_days(client, app, settings):
    _enable_hive(client)
    layout = storage_layout(app.state.session_factory(), settings)
    folder = layout.series_dir / "Old (2006) [tmdbid-7]" / "Season 01"
    folder.mkdir(parents=True, exist_ok=True)
    video = folder / "e.mkv"
    old_size = _write_publishable_video(video, prefix=b"o")

    db = app.state.session_factory()
    try:
        _join_fixture_ruche(db, _origin_id(settings))
        media = Media(type="series", title="Old", year=2006, tmdb_id=7)
        db.add(media)
        db.flush()
        item = MediaItem(
            media_id=media.id,
            season=1,
            episode=1,
            state="downloaded",
            downloaded=True,
            file_path=str(video),
        )
        db.add(item)
        db.flush()
        db.add(
            LibraryFile(
                path=str(video),
                size=old_size,
                parsed_title="Old",
                parsed_year=2006,
                season=1,
                episode=1,
                tmdb_id=7,
                media_type="series",
                media_id=media.id,
                media_item_id=item.id,
                status="matched",
                confidence="high",
                seen_at=NOW,
            )
        )
        db.commit()
        item_id = item.id
    finally:
        db.close()

    db = app.state.session_factory()
    try:
        _hash_until_done(db, settings, item_id)
        sync_hive_catalog(db, settings, now=NOW)
        db.commit()
        rows = list(db.scalars(select(HiveEntry).where(HiveEntry.media_item_id == item_id)).all())
        assert rows
        for row in rows:
            row.etat = "retire"
            row.tombstone_at = NOW - timedelta(days=31)
        db.commit()
        n = purge_old_tombstones(db, now=NOW)
        db.commit()
        assert n >= 1
        assert db.scalar(select(HiveEntry).where(HiveEntry.media_item_id == item_id).limit(1)) is None
    finally:
        db.close()


def test_trash_emits_tombstone(client, app, settings):
    _enable_hive(client)
    layout = storage_layout(app.state.session_factory(), settings)
    folder = layout.series_dir / "Trash (2005) [tmdbid-6]" / "Season 01"
    folder.mkdir(parents=True, exist_ok=True)
    video = folder / "e.mkv"
    trash_size = _write_publishable_video(video, prefix=b"t")

    db = app.state.session_factory()
    try:
        _join_fixture_ruche(db, _origin_id(settings))
        media = Media(type="series", title="Trash", year=2005, tmdb_id=6)
        db.add(media)
        db.flush()
        item = MediaItem(
            media_id=media.id,
            season=1,
            episode=1,
            state="downloaded",
            downloaded=True,
            file_path=str(video),
        )
        db.add(item)
        db.flush()
        lf = LibraryFile(
            path=str(video),
            size=trash_size,
            parsed_title="Trash",
            parsed_year=2005,
            season=1,
            episode=1,
            tmdb_id=6,
            media_type="series",
            media_id=media.id,
            media_item_id=item.id,
            status="matched",
            confidence="high",
            seen_at=NOW,
        )
        db.add(lf)
        db.commit()
        item_id = item.id
        lf_id = lf.id
    finally:
        db.close()

    db = app.state.session_factory()
    try:
        _hash_until_done(db, settings, item_id)
        sync_hive_catalog(db, settings, now=NOW)
        db.commit()
    finally:
        db.close()

    db = app.state.session_factory()
    try:
        tp = layout.trash_dir / "st" / "e.mkv"
        tp.parent.mkdir(parents=True, exist_ok=True)
        tp.write_bytes(b"t")
        te = TrashEntry(
            original_path=str(video),
            trash_path=str(tp.resolve()),
            size=1,
            media_item_id=item_id,
            label="t",
            deleted_at=NOW,
            purge_after=NOW + timedelta(days=7),
        )
        db.add(te)
        lf = db.get(LibraryFile, lf_id)
        lf.status = "trashed"
        db.commit()
        sync_hive_catalog(db, settings, now=NOW)
        db.commit()
        last = db.scalar(
            select(HiveEntry)
            .where(HiveEntry.media_item_id == item_id)
            .order_by(HiveEntry.seq.desc())
            .limit(1)
        )
        assert last is not None
        assert last.etat == "retire"
    finally:
        db.close()
