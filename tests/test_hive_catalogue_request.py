"""Recette Hive : request catalogue, métadonnées publish, empreinte worker."""

from __future__ import annotations

import json

from sqlalchemy import select
from tests.conftest import (
    catalog_min_logical_video_bytes,
    catalog_unknown_legacy_min_bytes,
    csrf,
    login,
    write_sparse_video_file,
)
from tests.test_hive_profile_attempt import _seed_catalogue_row, _vff_principal
from tests.test_hive_t6 import NOW, _enable, _ruche

from mediatracker.core.worker_schedule import next_deadline
from mediatracker.db.models import (
    Download,
    DownloadHiveFetch,
    HiveCatalogue,
    HiveEntry,
    HiveFileFingerprint,
    HiveShareIntent,
    Media,
    MediaItem,
)
from mediatracker.hive.catalog import _fiche_document, ensure_hive_release_metadata, publish_available
from mediatracker.hive.fingerprint import has_pending_hive_fingerprint
from mediatracker.hive.hive_catalogue import _stable_work_key, refresh_hive_catalogue_store
from mediatracker.hive.peer_gossip import origin_is_reachable
from mediatracker.hive.source_catalog import catalogue_request_miss_detail, pick_catalog_match_for_profile_attempt
from mediatracker.hive.source_download import try_enqueue_hive_for_item

pytest_plugins = ["tests.test_hive_t1", "tests.test_hive_profile_attempt"]


def _ensure_hive_enqueue_prereqs(db, ruche_hex: str) -> None:
    from mediatracker.core.quality_profile import ensure_default_profile
    from mediatracker.db.models import HiveMember, Setting
    from mediatracker.hive.identity import HIVE_ENABLED_KEY

    ensure_default_profile(db)
    db.merge(Setting(key=HIVE_ENABLED_KEY, value="true", is_secret=False, updated_at=NOW))
    db.merge(
        HiveMember(
            ruche_id=bytes.fromhex(ruche_hex),
            member_instance_id="hv1test-test-test-test-test-test-test",
            public_address="https://peer.exemple.test",
            joined_at=NOW,
        )
    )


def test_ensure_release_metadata_uses_media_prefs_without_item_quality(db):
    media = Media(type="film", title="Film fictif", year=2099, tmdb_id=9909, pref_quality="1080p", pref_language="VF")
    item = MediaItem(media_id=0, state="downloaded")
    db.add(media)
    db.flush()
    item.media_id = media.id
    db.add(item)
    db.commit()
    ensure_hive_release_metadata(media, item)
    assert item.release_quality == "1080p"
    assert item.release_language == "VF"


def test_ensure_release_metadata_from_filename(db):
    media = Media(type="film", title="Film fictif", year=2099, tmdb_id=9910, pref_quality="HD", pref_language="VFF")
    item = MediaItem(
        media_id=0,
        state="downloaded",
        file_path="/data/film.1080p.VFF.x264.mkv",
    )
    db.add(media)
    db.flush()
    item.media_id = media.id
    db.add(item)
    db.commit()
    ensure_hive_release_metadata(media, item)
    assert item.release_quality
    assert item.release_quality.casefold() != "unknown"
    assert item.release_language
    assert item.release_language.casefold() != "unknown"


def test_fiche_document_rejects_non_tmdb_poster(db):
    media = Media(
        type="film",
        title="Film fictif",
        year=2099,
        tmdb_id=9911,
        poster_url="https://evil.example.test/poster.jpg",
    )
    item = MediaItem(media_id=0, state="missing")
    db.add(media)
    db.flush()
    item.media_id = media.id
    db.add(item)
    db.commit()
    doc = _fiche_document(media, item)
    assert "poster_url" not in doc


def test_fiche_document_includes_poster_and_overview(db):
    media = Media(
        type="film",
        title="Film fictif",
        year=2099,
        tmdb_id=9911,
        poster_url="https://image.tmdb.org/t/p/w342/placeholder.jpg",
        overview="Résumé fictif court.",
    )
    item = MediaItem(media_id=0, state="missing")
    db.add(media)
    db.flush()
    item.media_id = media.id
    db.add(item)
    db.commit()
    doc = _fiche_document(media, item, taille=1000)
    assert doc["poster_url"] == media.poster_url
    assert doc["overview"] == "Résumé fictif court."


def test_post_catalogue_request_miss_detail_profile(db, ruche_hex):
    media = Media(type="film", title="Film fictif", year=2099, tmdb_id=9912, pref_quality="HD", pref_language="VFF")
    item = MediaItem(media_id=0, season=None, episode=None, state="missing")
    db.add(media)
    db.flush()
    item.media_id = media.id
    db.add(item)
    db.commit()
    _seed_catalogue_row(
        db,
        cle={"type": "film", "tmdb_id": 9912, "version": "4K+VO"},
        version="4K+VO",
        sha="a" * 64,
        joignable=True,
        ruche_hex=ruche_hex,
    )
    profile = _vff_principal()
    detail = catalogue_request_miss_detail(db, media, item, profile=profile)
    assert "profil" in detail.casefold()


def test_post_catalogue_request_no_catch_up_on_miss(client):
    login(client)
    _enable(client)
    rid = _ruche(client)
    db = client.app.state.session_factory()
    try:
        cle = {"type": "film", "tmdb_id": 9913, "version": "4K+VO"}
        media = Media(
            type="film",
            title="Film fictif",
            year=2099,
            tmdb_id=9913,
            followed=True,
            new_episode_policy="propose",
        )
        db.add(media)
        db.flush()
        db.add(MediaItem(media_id=media.id, state="missing"))
        _seed_catalogue_row(
            db,
            cle=cle,
            version="4K+VO",
            sha="d" * 64,
            joignable=True,
            ruche_hex=rid.hex(),
        )
        refresh_hive_catalogue_store(db, now=NOW)
        db.commit()
        work_key = _stable_work_key(cle)
    finally:
        db.close()
    before = client.post(
        f"/api/v1/hive/catalogue/{work_key}/request",
        json={},
        headers=csrf(client),
    )
    assert before.status_code == 422
    assert "profil" in before.json()["detail"].casefold()
    db = client.app.state.session_factory()
    try:
        media = db.scalar(select(Media).where(Media.tmdb_id == 9913).limit(1))
        assert media is not None
        assert media.catch_up_pending is not True
        assert media.catch_up_pending is not True
    finally:
        db.close()


def test_post_catalogue_request_honors_version_key(client):
    login(client)
    _enable(client)
    rid = _ruche(client)
    db = client.app.state.session_factory()
    try:
        cle = {"type": "film", "tmdb_id": 9914, "version": "HD+VFF"}
        media = Media(
            type="film",
            title="Film fictif",
            year=2099,
            tmdb_id=9914,
            followed=True,
            new_episode_policy="propose",
        )
        db.add(media)
        db.flush()
        db.add(MediaItem(media_id=media.id, state="missing"))
        for version, sha, fid in (
            ("HD+VFF", "e" * 64, "e0" * 16),
            ("HD+VO", "f" * 64, "f0" * 16),
        ):
            _seed_catalogue_row(
                db,
                cle={**cle, "version": version},
                version=version,
                sha=sha,
                joignable=True,
                ruche_hex=rid.hex(),
                file_id_hex=fid,
            )
        refresh_hive_catalogue_store(db, now=NOW)
        db.commit()
        work_key = _stable_work_key(cle)
    finally:
        db.close()
    miss = client.post(
        f"/api/v1/hive/catalogue/{work_key}/request",
        json={"version_key": "HD+XX"},
        headers=csrf(client),
    )
    assert miss.status_code == 422
    assert "absente" in miss.json()["detail"].casefold()


def test_worker_deadline_includes_pending_fingerprint(client, settings, app):
    _enable(client)
    db = app.state.session_factory()
    try:
        media = Media(type="film", title="Film fictif", tmdb_id=9915, followed=True)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, state="downloaded", file_path="/tmp/fictif.bin")
        db.add(item)
        db.flush()
        db.add(HiveShareIntent(media_item_id=item.id))
        db.add(
            HiveFileFingerprint(
                media_item_id=item.id,
                path="/tmp/fictif.bin",
                size=10_000,
                mtime_ns=1,
                partial_size=100,
                updated_at=NOW,
            )
        )
        db.commit()
        assert has_pending_hive_fingerprint(db)
        assert next_deadline(db, settings, NOW) == NOW
    finally:
        db.close()


def test_publish_available_republishes_when_version_label_changes(client, db, ruche_hex):
    login(client)
    client.post("/api/v1/hive/identity/reset", json={"name": "re"}, headers=csrf(client))
    from mediatracker.db.models import HiveEntry
    from mediatracker.hive.chunk_digest import file_sha256_hex
    from mediatracker.hive.identity import hive_public_id_from_public_key, load_private_key

    settings = client.app.state.settings
    pk = load_private_key(settings)
    assert pk is not None
    origin = hive_public_id_from_public_key(pk.public_key())
    ruche_id = bytes.fromhex(ruche_hex)
    media = Media(type="film", title="Film fictif", year=2020, tmdb_id=9917, pref_quality="HD", pref_language="VFF")
    db.add(media)
    db.flush()
    legacy_bytes = catalog_unknown_legacy_min_bytes()
    video = settings.data_dir / "fictif-repub.bin"
    write_sparse_video_file(video, legacy_bytes, prefix=b"r")
    st = video.stat()
    mtime_ns = getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))
    sha = file_sha256_hex(video)
    item = MediaItem(
        media_id=media.id,
        state="downloaded",
        downloaded=True,
        file_path=str(video.resolve()),
        release_quality="unknown",
        release_language="unknown",
    )
    db.add(item)
    db.flush()
    db.add(
        HiveFileFingerprint(
            media_item_id=item.id,
            path=str(video.resolve()),
            size=legacy_bytes,
            mtime_ns=mtime_ns,
            sha256_hex=sha,
            partial_size=legacy_bytes,
            updated_at=NOW,
        )
    )
    db.add(
        HiveEntry(
            ruche_id=ruche_id,
            origin_instance_id=origin,
            seq=1,
            cle_json='{"type":"film","tmdb_id":9917,"version":"unknown+unknown"}',
            fiche_json='{"title":"Film fictif","tmdb_id":9917,"type":"film"}',
            sha256_hex=sha,
            file_id=bytes.fromhex("aa" * 16),
            etat="disponible",
            payload_json=f'{{"mtime_ns":{mtime_ns},"taille":{legacy_bytes}}}',
            signature_b64="dGVzdA==",
            media_item_id=item.id,
        )
    )
    db.commit()
    before = db.scalar(select(HiveEntry).where(HiveEntry.media_item_id == item.id))
    assert before is not None
    publish_available(db, settings, ruche_id=ruche_id, media=media, item=item, fingerprint_hex=sha, now=NOW)
    db.commit()
    entries = list(db.scalars(select(HiveEntry).where(HiveEntry.media_item_id == item.id).order_by(HiveEntry.seq)))
    assert len(entries) == 3
    assert entries[-1].etat == "disponible"
    assert "HD+VFF" in (entries[-1].cle_json or "")
    assert any(e.etat == "retire" for e in entries)


OVERSIZE_BYTES = 300 * 1024 * 1024 * 1024


def test_miss_detail_already_in_library(client, settings):
    login(client)
    _enable(client)
    rid = _ruche(client)
    db = client.app.state.session_factory()
    try:
        cle = {"type": "film", "tmdb_id": 9920, "version": "HD+VFF"}
        media = Media(
            type="film",
            title="Film fictif",
            year=2099,
            tmdb_id=9920,
            followed=True,
            new_episode_policy="propose",
        )
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, state="downloaded", downloaded=True, file_path="/tmp/fictif-lib.bin")
        db.add(item)
        _seed_catalogue_row(
            db,
            cle=cle,
            version="1080p+VFF",
            sha="1" * 64,
            joignable=True,
            ruche_hex=rid.hex(),
        )
        refresh_hive_catalogue_store(db, now=NOW)
        db.commit()
        work_key = _stable_work_key(cle)
    finally:
        db.close()
    resp = client.post(
        f"/api/v1/hive/catalogue/{work_key}/request",
        json={},
        headers=csrf(client),
    )
    assert resp.status_code == 422
    assert "bibliothèque" in resp.json()["detail"].casefold()


def test_miss_detail_bitrate_without_explicit_version(db, ruche_hex):
    media = Media(type="film", title="Film fictif", year=2099, tmdb_id=9921, pref_quality="HD", pref_language="VFF")
    item = MediaItem(media_id=0, season=None, episode=None, state="missing")
    db.add(media)
    db.flush()
    item.media_id = media.id
    db.add(item)
    db.commit()
    _seed_catalogue_row(
        db,
        cle={"type": "film", "tmdb_id": 9921, "version": "HD+VFF"},
        version="HD+VFF",
        sha="2" * 64,
        joignable=True,
        ruche_hex=ruche_hex,
        taille=OVERSIZE_BYTES,
    )
    profile = _vff_principal()
    detail = catalogue_request_miss_detail(db, media, item, profile=profile)
    assert "disproportionnée" in detail.casefold() or "taille" in detail.casefold()


def test_miss_detail_frozen_transfer(db, ruche_hex):
    from mediatracker.db.models import DownloadHiveFetch

    media = Media(type="film", title="Film fictif", year=2099, tmdb_id=9922, pref_quality="HD", pref_language="VFF")
    item = MediaItem(media_id=0, season=None, episode=None, state="missing")
    db.add(media)
    db.flush()
    item.media_id = media.id
    db.add(item)
    db.flush()
    dl = Download(media_item_id=item.id, state="downloading", created_at=NOW)
    db.add(dl)
    db.flush()
    db.add(
        DownloadHiveFetch(
            download_id=dl.id,
            sha256_hex="3" * 64,
            sources_json="[]",
            ruche_id=bytes.fromhex(ruche_hex),
            file_id=b"\x01" * 16,
            origin_instance_id="hv1x",
            peer_base_url="https://peer.exemple.test",
            expected_size=1000,
            updated_at=NOW,
        )
    )
    db.commit()
    _seed_catalogue_row(
        db,
        cle={"type": "film", "tmdb_id": 9922, "version": "HD+VFF"},
        version="HD+VFF",
        sha="4" * 64,
        joignable=True,
        ruche_hex=ruche_hex,
    )
    profile = _vff_principal()
    detail = catalogue_request_miss_detail(
        db, media, item, profile=profile, version_key="HD+VFF", explicit_version=False
    )
    assert "téléchargement" in detail.casefold() or "transfert" in detail.casefold()


def test_miss_detail_missing_sha(db, ruche_hex):
    from mediatracker.db.models import HiveCatalogue
    from mediatracker.hive.hive_catalogue import _stable_work_key

    media = Media(type="film", title="Film fictif", year=2099, tmdb_id=9923, pref_quality="HD", pref_language="VFF")
    item = MediaItem(media_id=0, season=None, episode=None, state="missing")
    db.add(media)
    db.flush()
    item.media_id = media.id
    db.add(item)
    cle = {"type": "film", "tmdb_id": 9923, "version": "HD+VFF"}
    db.add(
        HiveCatalogue(
            work_key=_stable_work_key(cle),
            version_key="HD+VFF",
            cle_json='{"type":"film","tmdb_id":9923,"version":"HD+VFF"}',
            sources_json=(
                f'[{{"ruche_id_hex":"{ruche_hex}","origine":"hv1x","file_id_hex":"{"cc" * 16}",'
                '"joignable":true}}]'
            ),
            disponible=True,
            refreshed_at=NOW,
        )
    )
    db.commit()
    profile = _vff_principal()
    detail = catalogue_request_miss_detail(db, media, item, profile=profile, version_key="HD+VFF")
    assert "empreinte" in detail.casefold() or "sha" in detail.casefold()


def test_explicit_request_preempts_classic_download_db(db, settings, ruche_hex):
    media = Media(
        type="film",
        title="Film fictif",
        year=2099,
        tmdb_id=9925,
        followed=True,
        pref_quality="HD",
        pref_language="VFF",
    )
    db.add(media)
    db.flush()
    item = MediaItem(media_id=media.id, state="missing")
    db.add(item)
    db.flush()
    db.add(Download(media_item_id=item.id, state="queued", created_at=NOW))
    _seed_catalogue_row(
        db,
        cle={"type": "film", "tmdb_id": 9925, "version": "1080p+VFF"},
        version="1080p+VFF",
        sha="6" * 64,
        joignable=True,
        ruche_hex=ruche_hex,
    )
    _ensure_hive_enqueue_prereqs(db, ruche_hex)
    db.commit()
    result = try_enqueue_hive_for_item(db, settings, media=media, item=item, now=NOW, version_key="1080p+VFF")
    assert result.outcome == "queued", result.detail
    dl = db.scalar(select(Download).where(Download.media_item_id == item.id))
    assert dl is not None and dl.state == "cancelled"


def test_explicit_request_preempts_classic_download(client, settings, ruche_hex):
    login(client)
    _enable(client)
    rid = _ruche(client)
    db = client.app.state.session_factory()
    try:
        cle = {"type": "film", "tmdb_id": 9925, "version": "1080p+VFF"}
        media = Media(
            type="film",
            title="Film fictif",
            year=2099,
            tmdb_id=9925,
            followed=True,
            new_episode_policy="propose",
        )
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, state="missing")
        db.add(item)
        db.flush()
        db.add(Download(media_item_id=item.id, state="queued", created_at=NOW))
        _seed_catalogue_row(
            db,
            cle=cle,
            version="1080p+VFF",
            sha="6" * 64,
            joignable=True,
            ruche_hex=rid.hex(),
        )
        _ensure_hive_enqueue_prereqs(db, rid.hex())
        db.commit()
        work_key = _stable_work_key(cle)
        item_id = item.id
    finally:
        db.close()
    resp = client.post(
        f"/api/v1/hive/catalogue/{work_key}/request",
        json={"version_key": "1080p+VFF"},
        headers=csrf(client),
    )
    assert resp.status_code == 200, resp.text
    if resp.status_code == 200:
        db = client.app.state.session_factory()
        try:
            dl = db.scalar(select(Download).where(Download.media_item_id == item_id))
            assert dl is not None
            assert dl.state == "cancelled"
            assert db.scalar(select(DownloadHiveFetch).limit(1)) is not None
        finally:
            db.close()


def test_explicit_request_replaces_conflicting_hive_download(client, settings, ruche_hex):
    login(client)
    _enable(client)
    rid = _ruche(client)
    db = client.app.state.session_factory()
    try:
        cle = {"type": "film", "tmdb_id": 9926, "version": "1080p+VFF"}
        media = Media(
            type="film",
            title="Film fictif",
            year=2099,
            tmdb_id=9926,
            followed=True,
            new_episode_policy="propose",
        )
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, state="missing")
        db.add(item)
        db.flush()
        old_dl = Download(media_item_id=item.id, state="downloading", created_at=NOW)
        db.add(old_dl)
        db.flush()
        db.add(
            DownloadHiveFetch(
                download_id=old_dl.id,
                sha256_hex="7" * 64,
                sources_json="[]",
                ruche_id=rid,
                file_id=b"\x07" * 16,
                origin_instance_id="hv1x",
                peer_base_url="https://peer.exemple.test",
                expected_size=1000,
                updated_at=NOW,
            )
        )
        _seed_catalogue_row(
            db,
            cle=cle,
            version="1080p+VFF",
            sha="8" * 64,
            joignable=True,
            ruche_hex=rid.hex(),
        )
        _ensure_hive_enqueue_prereqs(db, rid.hex())
        db.commit()
        work_key = _stable_work_key(cle)
        old_dl_id = old_dl.id
    finally:
        db.close()
    resp = client.post(
        f"/api/v1/hive/catalogue/{work_key}/request",
        json={"version_key": "1080p+VFF"},
        headers=csrf(client),
    )
    assert resp.status_code == 200, resp.text
    db = client.app.state.session_factory()
    try:
        old = db.get(Download, old_dl_id)
        assert old is not None and old.state == "cancelled"
        ctx = db.scalar(
            select(DownloadHiveFetch).where(DownloadHiveFetch.sha256_hex == "8" * 64).limit(1)
        )
        assert ctx is not None
    finally:
        db.close()


def test_explicit_version_key_skips_bitrate_filter(db, settings, ruche_hex):
    media = Media(type="film", title="Film fictif", year=2099, tmdb_id=9924, pref_quality="HD", pref_language="VFF")
    item = MediaItem(media_id=0, season=None, episode=None, state="missing")
    db.add(media)
    db.flush()
    item.media_id = media.id
    db.add(item)
    db.commit()
    _seed_catalogue_row(
        db,
        cle={"type": "film", "tmdb_id": 9924, "version": "1080p+VFF"},
        version="1080p+VFF",
        sha="5" * 64,
        joignable=True,
        ruche_hex=ruche_hex,
        taille=OVERSIZE_BYTES,
    )
    profile = _vff_principal()
    result = try_enqueue_hive_for_item(db, settings, media=media, item=item, now=NOW, version_key="1080p+VFF")
    assert result.outcome in ("queued", "pending")
    detail = catalogue_request_miss_detail(
        db, media, item, profile=profile, version_key="1080p+VFF", explicit_version=True
    )
    assert "utilisable" in detail.casefold() or "transfert" in detail.casefold()


def test_publish_records_local_reachability(client, db, ruche_hex):
    login(client)
    client.post("/api/v1/hive/identity/reset", json={"name": "pub"}, headers=csrf(client))
    from mediatracker.hive.chunk_digest import file_sha256_hex
    from mediatracker.hive.identity import hive_public_id_from_public_key, load_private_key

    settings = client.app.state.settings
    pk = load_private_key(settings)
    assert pk
    origin = hive_public_id_from_public_key(pk.public_key())
    ruche_id = bytes.fromhex(ruche_hex)
    try:
        min_bytes = catalog_min_logical_video_bytes()
        video = settings.data_dir / "fictif-publish.bin"
        write_sparse_video_file(video, min_bytes, prefix=b"p")
        st = video.stat()
        mtime_ns = getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))
        sha = file_sha256_hex(video)
        media = Media(type="film", title="Film fictif", year=2020, tmdb_id=9916)
        db.add(media)
        db.flush()
        item = MediaItem(
            media_id=media.id,
            state="downloaded",
            downloaded=True,
            file_path=str(video.resolve()),
            release_quality="HD",
            release_language="VFF",
        )
        db.add(item)
        db.flush()
        db.add(
            HiveFileFingerprint(
                media_item_id=item.id,
                path=str(video.resolve()),
                size=min_bytes,
                mtime_ns=mtime_ns,
                sha256_hex=sha,
                partial_size=min_bytes,
                updated_at=NOW,
            )
        )
        db.commit()
        publish_available(
            db,
            settings,
            ruche_id=ruche_id,
            media=media,
            item=item,
            fingerprint_hex=sha,
            now=NOW,
        )
        db.commit()
        assert origin_is_reachable(db, ruche_id=ruche_id, origin_id=origin, now=NOW)
    finally:
        db.rollback()


def test_pick_catalog_explicit_skips_profile_filter(db, ruche_hex):
    media = Media(type="film", title="Film fictif", year=2099, tmdb_id=9930, pref_quality="HD", pref_language="VFF")
    item = MediaItem(media_id=0, season=None, episode=None, state="missing")
    db.add(media)
    db.flush()
    item.media_id = media.id
    db.add(item)
    db.commit()
    _seed_catalogue_row(
        db,
        cle={"type": "film", "tmdb_id": 9930, "version": "4K+VO"},
        version="4K+VO",
        sha="9" * 64,
        joignable=True,
        ruche_hex=ruche_hex,
    )
    profile = _vff_principal()
    assert pick_catalog_match_for_profile_attempt(db, media, item, profile=profile) is None
    match = pick_catalog_match_for_profile_attempt(
        db,
        media,
        item,
        profile=profile,
        version_key="4K+VO",
        skip_profile_filter=True,
    )
    assert match is not None
    assert match.row.version_key == "4K+VO"


def test_explicit_request_skips_profile_filter_http(client):
    login(client)
    _enable(client)
    rid = _ruche(client)
    db = client.app.state.session_factory()
    try:
        cle = {"type": "film", "tmdb_id": 9931, "version": "4K+VOSTFR"}
        media = Media(
            type="film",
            title="Film fictif",
            year=2099,
            tmdb_id=9931,
            followed=True,
            pref_quality="HD",
            pref_language="VFF",
        )
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, state="missing")
        db.add(item)
        _seed_catalogue_row(
            db,
            cle=cle,
            version="4K+VOSTFR",
            sha="a1" * 32,
            joignable=True,
            ruche_hex=rid.hex(),
        )
        _ensure_hive_enqueue_prereqs(db, rid.hex())
        refresh_hive_catalogue_store(db, now=NOW)
        db.commit()
        work_key = _stable_work_key(cle)
    finally:
        db.close()
    auto = client.post(f"/api/v1/hive/catalogue/{work_key}/request", json={}, headers=csrf(client))
    assert auto.status_code == 422
    assert "profil" in auto.json()["detail"].casefold()
    explicit = client.post(
        f"/api/v1/hive/catalogue/{work_key}/request",
        json={"version_key": "4K+VOSTFR"},
        headers=csrf(client),
    )
    assert explicit.status_code in (200, 409), explicit.text
    assert explicit.status_code != 422


def test_catalogue_profile_compatible_flags(client):
    login(client)
    _enable(client)
    rid = _ruche(client)
    db = client.app.state.session_factory()
    try:
        cle = {"type": "film", "tmdb_id": 9932, "version": "1080p+VFF"}
        media = Media(
            type="film",
            title="Film fictif",
            year=2099,
            tmdb_id=9932,
            followed=True,
            pref_quality="HD",
            pref_language="VFF",
        )
        db.add(media)
        db.flush()
        db.add(MediaItem(media_id=media.id, state="missing"))
        _seed_catalogue_row(
            db,
            cle=cle,
            version="1080p+VFF",
            sha="b1" * 32,
            joignable=True,
            ruche_hex=rid.hex(),
            taille=5_000_000,
            file_id_hex="b1" * 16,
        )
        _seed_catalogue_row(
            db,
            cle={**cle, "version": "4K+VO"},
            version="4K+VO",
            sha="b2" * 32,
            joignable=True,
            ruche_hex=rid.hex(),
            taille=5_000_000,
            file_id_hex="b2" * 16,
        )
        db.commit()
    finally:
        db.close()
    page = client.get("/api/v1/hive/catalogue?tmdb_id=9932&media_type=film").json()
    work = next(w for w in page["items"] if w["tmdb_id"] == 9932)
    by_key = {v["version_key"]: v for v in work["versions"]}
    assert by_key["1080p+VFF"]["profile_compatible"] is True
    assert by_key["1080p+VFF"]["recommended"] is True
    assert by_key["4K+VO"]["profile_compatible"] is False
    assert by_key["4K+VO"]["recommended"] is False


def test_catalogue_filter_tmdb_id_requires_media_type_when_ambiguous(client):
    login(client)
    _enable(client)
    rid = _ruche(client)
    db = client.app.state.session_factory()
    shared_tmdb = 99901
    try:
        seeds = (
            ({"type": "film", "tmdb_id": shared_tmdb, "version": "HD+VFF"}, "d1" * 32),
            (
                {"type": "series", "tmdb_id": shared_tmdb, "saison": 1, "episode": 1, "version": "HD+VFF"},
                "d2" * 32,
            ),
        )
        for idx, (cle, sha) in enumerate(seeds):
            sources = [
                {
                    "ruche_id_hex": rid.hex(),
                    "origine": "hv1test-test-test-test-test-test-test",
                    "file_id_hex": format(idx, "032x"),
                    "sha256": sha,
                    "joignable": True,
                }
            ]
            db.add(
                HiveCatalogue(
                    work_key=_stable_work_key(cle),
                    version_key="HD+VFF",
                    cle_json=json.dumps(cle, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    sources_json=json.dumps(sources, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    disponible=True,
                    refreshed_at=NOW,
                )
            )
            fiche = {"taille": 5_000_000, "type": cle.get("type"), "tmdb_id": cle.get("tmdb_id")}
            db.add(
                HiveEntry(
                    ruche_id=bytes.fromhex(rid.hex()),
                    origin_instance_id=sources[0]["origine"],
                    seq=idx + 1,
                    cle_json=json.dumps(cle, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    fiche_json=json.dumps(fiche, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    sha256_hex=sha,
                    file_id=bytes.fromhex(sources[0]["file_id_hex"]),
                    etat="disponible",
                    payload_json=json.dumps(
                        {"taille": 5_000_000, "fiche": fiche},
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                )
            )
        refresh_hive_catalogue_store(db, now=NOW)
        db.commit()
    finally:
        db.close()
    res = client.get(f"/api/v1/hive/catalogue?tmdb_id={shared_tmdb}")
    assert res.status_code == 422
    assert "media_type" in res.json()["detail"].casefold()


def test_catalogue_filter_tmdb_id_unknown_returns_empty(client):
    login(client)
    _enable(client)
    res = client.get("/api/v1/hive/catalogue?tmdb_id=99902")
    assert res.status_code == 200
    assert res.json()["items"] == []


def test_catalogue_filter_tmdb_id(client):
    login(client)
    _enable(client)
    rid = _ruche(client)
    db = client.app.state.session_factory()
    try:
        for tmdb_id in (9933, 9934):
            cle = {"type": "film", "tmdb_id": tmdb_id, "version": "HD+VFF"}
            _seed_catalogue_row(
                db,
                cle=cle,
                version="HD+VFF",
                sha="c1" * 32,
                joignable=True,
                ruche_hex=rid.hex(),
            )
        refresh_hive_catalogue_store(db, now=NOW)
        db.commit()
    finally:
        db.close()
    page = client.get("/api/v1/hive/catalogue?tmdb_id=9933&media_type=film").json()
    assert page["items"]
    assert all(row.get("tmdb_id") == 9933 for row in page["items"])
    assert not any(row.get("tmdb_id") == 9934 for row in page["items"])
