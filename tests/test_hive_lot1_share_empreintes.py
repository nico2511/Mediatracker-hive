"""Lot 1 Hive : portée fiche/saison, volet empreintes (vérif lecture seule, recalcul sûr)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from tests.conftest import PLAUSIBLE_VIDEO_BYTES, csrf, write_sparse_video_file

from mediatracker.core.storage import storage_layout
from mediatracker.db.models import (
    HiveFileFingerprint,
    HiveShareIntent,
    LibraryFile,
    Media,
    MediaItem,
    Setting,
)
from mediatracker.hive.catalog import sync_hive_catalog
from mediatracker.hive.share_policy import HIVE_CATALOG_AUTO_SHARE_KEY, grant_share_intent, revoke_share_intent

pytest_plugins = ["tests.test_hive_t1"]

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)


def _force_manual_share_mode(db) -> None:
    db.merge(Setting(key=HIVE_CATALOG_AUTO_SHARE_KEY, value="false"))
    db.flush()


def _enable_hive(client):
    from tests.test_hive_t2 import _enable_hive as base

    base(client)


def _origin_join(client, app, settings):
    from tests.test_hive_catalog_sharing_worker import _join_ruche, _origin_id

    origin = _origin_id(settings)
    ruche_id = bytes([0xB1] * 15 + [1])
    db = app.state.session_factory()
    try:
        _join_ruche(db, origin, ruche_id, settings)
        db.commit()
    finally:
        db.close()
    return ruche_id


def _series_items(db, app, settings, *, tmdb_id: int, episodes: list[tuple[int, int]], hash_eps: set[int]):
    from tests.test_hive_t2 import _hash_until_done

    layout = storage_layout(app.state.session_factory(), settings)
    folder = layout.series_dir / f"Show (2099) [tmdbid-{tmdb_id}]" / "Season 01"
    folder.mkdir(parents=True, exist_ok=True)
    media = Media(type="series", title="Show fictif", year=2099, tmdb_id=tmdb_id)
    db.add(media)
    db.flush()
    items: list[MediaItem] = []
    for season, episode in episodes:
        video = folder / f"S{season:02d}E{episode:02d}.mkv"
        write_sparse_video_file(video, PLAUSIBLE_VIDEO_BYTES, prefix=f"{season}{episode}".encode())
        item = MediaItem(
            media_id=media.id,
            season=season,
            episode=episode,
            title=f"Ep {episode}",
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
                parsed_title="Show fictif",
                parsed_year=2099,
                season=season,
                episode=episode,
                media_type="series",
                tmdb_id=tmdb_id,
                media_id=media.id,
                media_item_id=item.id,
                status="matched",
                confidence="high",
                seen_at=NOW,
            )
        )
        items.append(item)
    db.commit()
    for item in items:
        if (item.season, item.episode) in hash_eps:
            grant_share_intent(db, item.id)
            _hash_until_done(db, settings, int(item.id))
    sync_hive_catalog(db, settings, now=NOW)
    db.commit()
    return media, items


def _share_all(client, item_ids: list[int]):
    for iid in item_ids:
        r = client.patch(
            f"/api/v1/hive/items/{iid}/sharing",
            json={"exclude_all": False, "excluded_ruche_ids": []},
            headers=csrf(client),
        )
        assert r.status_code == 204, r.text


def test_fiche_unshare_removes_three_not_other_fiche(client, app, settings):
    """Trois épisodes sur la fiche A ; un quatrième fichier sur une autre fiche reste partagé."""
    _enable_hive(client)
    _origin_join(client, app, settings)
    db = app.state.session_factory()
    try:
        media_a, items_a = _series_items(
            db,
            app,
            settings,
            tmdb_id=91001,
            episodes=[(1, i) for i in range(1, 4)],
            hash_eps={(1, 1), (1, 2), (1, 3)},
        )
        media_b, items_b = _series_items(
            db,
            app,
            settings,
            tmdb_id=91011,
            episodes=[(1, 1)],
            hash_eps={(1, 1)},
        )
        ids_a = [int(it.id) for it in items_a]
        id_b = int(items_b[0].id)
        _share_all(client, ids_a + [id_b])
        media_id_a = int(media_a.id)
    finally:
        db.close()

    scope = client.get(f"/api/v1/hive/media/{media_id_a}/sharing-scope").json()
    assert scope["file_count"] == 3
    assert scope["any_shared"] is True

    r = client.patch(
        f"/api/v1/hive/media/{media_id_a}/sharing",
        json={"exclude_all": True, "excluded_ruche_ids": []},
        headers=csrf(client),
    )
    assert r.status_code == 204

    for iid in ids_a:
        body = client.get(f"/api/v1/hive/items/{iid}/sharing").json()
        assert body["exclude_all"] is True
        assert body["catalog_entry_local"] is False
    body_b = client.get(f"/api/v1/hive/items/{id_b}/sharing").json()
    assert body_b["exclude_all"] is False
    assert body_b["catalog_entry_local"] is True


def test_season_unshare_leaves_other_season_shared(client, app, settings):
    _enable_hive(client)
    _origin_join(client, app, settings)
    db = app.state.session_factory()
    try:
        media, items = _series_items(
            db,
            app,
            settings,
            tmdb_id=91002,
            episodes=[(1, 1), (1, 2), (2, 1)],
            hash_eps={(1, 1), (1, 2), (2, 1)},
        )
        ids = [int(it.id) for it in items]
        _share_all(client, ids)
        media_id = int(media.id)
        s2_id = next(int(it.id) for it in items if it.season == 2)
    finally:
        db.close()

    r = client.patch(
        f"/api/v1/hive/media/{media_id}/seasons/1/sharing",
        json={"exclude_all": True, "excluded_ruche_ids": []},
        headers=csrf(client),
    )
    assert r.status_code == 204

    db = app.state.session_factory()
    try:
        assert db.get(HiveShareIntent, s2_id) is not None
        s1 = [int(it.id) for it in items if it.season == 1]
        for iid in s1:
            assert db.get(HiveShareIntent, iid) is None
    finally:
        db.close()


def test_fiche_scope_shared_when_only_later_episode_shared(client, app, settings):
    _enable_hive(client)
    _origin_join(client, app, settings)
    db = app.state.session_factory()
    try:
        media, items = _series_items(
            db,
            app,
            settings,
            tmdb_id=91003,
            episodes=[(1, 1), (1, 2)],
            hash_eps={(1, 2)},
        )
        ep2 = next(int(it.id) for it in items if it.episode == 2)
        _share_all(client, [ep2])
        media_id = int(media.id)
    finally:
        db.close()

    scope = client.get(f"/api/v1/hive/media/{media_id}/sharing-scope").json()
    assert scope["any_shared"] is True
    assert scope["file_count"] == 2


def test_verify_readonly_and_no_valide_on_presence_only(client, app, settings):
    _enable_hive(client)
    db = app.state.session_factory()
    item_id: int
    try:
        from tests.test_hive_sharing_catalog_entry_local import _film_with_file

        media, item = _film_with_file(db, app, settings, tmdb_id=91004, write_hash=False)
        media_id = int(media.id)
        item_id = int(item.id)
        db.commit()
    finally:
        db.close()

    before = client.get(f"/api/v1/hive/media/{media_id}/empreintes").json()
    assert before["total"] == 1
    assert before["rows"][0]["file_state"] == "present"
    assert "valide" not in before["rows"][0]["file_detail"].lower()

    db = app.state.session_factory()
    try:
        fp_before = db.scalar(select(HiveFileFingerprint).where(HiveFileFingerprint.media_item_id == item_id))
        sha_before = (fp_before.sha256_hex if fp_before else None) or None
        intents_before = db.scalar(select(func.count()).select_from(HiveShareIntent))
    finally:
        db.close()

    verified = client.post(f"/api/v1/hive/media/{media_id}/empreintes/verify", headers=csrf(client)).json()
    assert verified["total"] == 1

    db = app.state.session_factory()
    try:
        fp_after = db.scalar(select(HiveFileFingerprint).where(HiveFileFingerprint.media_item_id == item_id))
        sha_after = (fp_after.sha256_hex if fp_after else None) or None
        intents_after = db.scalar(select(func.count()).select_from(HiveShareIntent))
        assert sha_before == sha_after
        assert intents_before == intents_after
    finally:
        db.close()


def test_recalculate_does_not_schedule_chunks_lot4(client, app, settings):
    _enable_hive(client)
    _origin_join(client, app, settings)

    db = app.state.session_factory()
    try:
        from tests.test_hive_sharing_catalog_entry_local import _film_with_file

        media, item = _film_with_file(db, app, settings, tmdb_id=91005, write_hash=True)
        grant_share_intent(db, item.id)
        sync_hive_catalog(db, settings, now=NOW)
        db.commit()
        media_id = int(media.id)
        item_id = int(item.id)
        fp = db.get(HiveFileFingerprint, item_id)
        assert fp and fp.sha256_hex
        fp.chunk_digests_json = None
        fp.chunk_digest_offset = 0
        fp.chunk_digest_claimed_at = None
        db.commit()
    finally:
        db.close()

    r = client.post(
        f"/api/v1/hive/media/{media_id}/empreintes/recalculate",
        headers=csrf(client),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["scheduled_chunks"] == 0
    assert body["in_progress_chunks"] == 0
    assert body["wake_sent"] is False


def test_recalculate_honest_when_active_claim(client, app, settings):
    _enable_hive(client)
    db = app.state.session_factory()
    try:
        from tests.test_hive_sharing_catalog_entry_local import _film_with_file

        _force_manual_share_mode(db)
        media, item = _film_with_file(db, app, settings, tmdb_id=91007, write_hash=True)
        grant_share_intent(db, item.id)
        item_id = int(item.id)
        fp = db.get(HiveFileFingerprint, item_id)
        fp.chunk_digests_json = None
        fp.chunk_digest_offset = 0
        fp.chunk_digest_claimed_at = datetime.now(timezone.utc)
        db.commit()
        media_id = int(media.id)
    finally:
        db.close()

    body = client.post(
        f"/api/v1/hive/media/{media_id}/empreintes/recalculate",
        headers=csrf(client),
    ).json()
    assert body["scheduled_chunks"] == 0
    assert body["in_progress_chunks"] == 0
    assert body["wake_sent"] is False


def test_recalculate_skips_chunk_without_share_intent(client, app, settings):
    _enable_hive(client)
    db = app.state.session_factory()
    try:
        from tests.test_hive_sharing_catalog_entry_local import _film_with_file

        _force_manual_share_mode(db)
        media, item = _film_with_file(db, app, settings, tmdb_id=91008, write_hash=True)
        item_id = int(item.id)
        revoke_share_intent(db, item_id)
        fp = db.get(HiveFileFingerprint, item_id)
        fp.chunk_digests_json = None
        fp.chunk_digest_offset = 0
        db.commit()
        media_id = int(media.id)
    finally:
        db.close()

    body = client.post(
        f"/api/v1/hive/media/{media_id}/empreintes/recalculate",
        headers=csrf(client),
    ).json()
    assert body["scheduled_chunks"] == 0
    assert body["skipped_no_intent"] >= 1
    assert body["wake_sent"] is False


def test_fiche_unshare_all_seasons_on_same_media(client, app, settings):
    _enable_hive(client)
    _origin_join(client, app, settings)
    db = app.state.session_factory()
    try:
        media, items = _series_items(
            db,
            app,
            settings,
            tmdb_id=91009,
            episodes=[(1, 1), (1, 2), (2, 1)],
            hash_eps={(1, 1), (1, 2), (2, 1)},
        )
        ids = [int(it.id) for it in items]
        _share_all(client, ids)
        media_id = int(media.id)
    finally:
        db.close()

    assert client.patch(
        f"/api/v1/hive/media/{media_id}/sharing",
        json={"exclude_all": True, "excluded_ruche_ids": []},
        headers=csrf(client),
    ).status_code == 204

    db = app.state.session_factory()
    try:
        for iid in ids:
            assert db.get(HiveShareIntent, iid) is None
    finally:
        db.close()


def test_fiche_unshare_patch_when_only_later_episode_shared(client, app, settings):
    _enable_hive(client)
    _origin_join(client, app, settings)
    db = app.state.session_factory()
    try:
        media, items = _series_items(
            db,
            app,
            settings,
            tmdb_id=91010,
            episodes=[(1, 1), (1, 2)],
            hash_eps={(1, 1), (1, 2)},
        )
        ep1 = next(int(it.id) for it in items if it.episode == 1)
        ep2 = next(int(it.id) for it in items if it.episode == 2)
        _share_all(client, [ep2])
        media_id = int(media.id)
    finally:
        db.close()

    assert client.patch(
        f"/api/v1/hive/media/{media_id}/sharing",
        json={"exclude_all": True, "excluded_ruche_ids": []},
        headers=csrf(client),
    ).status_code == 204

    db = app.state.session_factory()
    try:
        assert db.get(HiveShareIntent, ep2) is None
        assert db.get(HiveShareIntent, ep1) is None
    finally:
        db.close()


def test_fiche_unshare_persistent_under_auto_share(client, app, settings):
    from mediatracker.db.models import HiveShareExclusion, Setting
    from mediatracker.hive.share_exclusions import RUCHE_EXCLUDE_ALL
    from mediatracker.hive.share_policy import HIVE_CATALOG_AUTO_SHARE_KEY, item_wants_hive_share

    _enable_hive(client)
    _origin_join(client, app, settings)
    db = app.state.session_factory()
    try:
        db.merge(Setting(key=HIVE_CATALOG_AUTO_SHARE_KEY, value="true"))
        media, items = _series_items(
            db,
            app,
            settings,
            tmdb_id=91012,
            episodes=[(1, 1)],
            hash_eps={(1, 1)},
        )
        item_id = int(items[0].id)
        _share_all(client, [item_id])
        media_id = int(media.id)
    finally:
        db.close()

    assert client.patch(
        f"/api/v1/hive/media/{media_id}/sharing",
        json={"exclude_all": True, "excluded_ruche_ids": []},
        headers=csrf(client),
    ).status_code == 204

    db = app.state.session_factory()
    try:
        assert db.get(HiveShareIntent, item_id) is None
        ex = db.scalar(
            select(HiveShareExclusion).where(
                HiveShareExclusion.media_item_id == item_id,
                HiveShareExclusion.ruche_id == RUCHE_EXCLUDE_ALL,
            )
        )
        assert ex is not None
        assert item_wants_hive_share(db, item_id) is False
        sync_hive_catalog(db, settings, now=NOW)
        db.commit()
        assert item_wants_hive_share(db, item_id) is False
    finally:
        db.close()


def _seed_global_noise_backfill_candidates(db, count: int, *, older_than: datetime) -> None:
    """Remplit le scan global avec des candidats anciens hors périmètre cible."""
    for i in range(count):
        media = Media(type="film", title=f"Bruit{i}", year=2099, tmdb_id=920_000 + i)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, state="missing")
        db.add(item)
        db.flush()
        db.add(HiveShareIntent(media_item_id=item.id))
        db.add(
            HiveFileFingerprint(
                media_item_id=item.id,
                path=f"/tmp/noise-{i}.mkv",
                size=2048,
                sha256_hex="b" * 64,
                chunk_digests_json=None,
                chunk_digest_offset=0,
                updated_at=older_than,
            )
        )
    db.flush()


@pytest.mark.skip(reason="Backfill morceaux désactivé (LOT4+ fetch-grant seul).")
def test_recalculate_scope_not_capped_by_global_scan(client, app, settings):
    """>256 candidats globaux plus anciens n'empêchent pas le périmètre d'être programmé."""
    _enable_hive(client)
    _origin_join(client, app, settings)
    db = app.state.session_factory()
    try:
        from tests.test_hive_sharing_catalog_entry_local import _film_with_file

        _force_manual_share_mode(db)
        _seed_global_noise_backfill_candidates(db, 280, older_than=NOW - timedelta(days=30))
        media, item = _film_with_file(db, app, settings, tmdb_id=91013, write_hash=True)
        grant_share_intent(db, item.id)
        item_id = int(item.id)
        fp = db.get(HiveFileFingerprint, item_id)
        fp.chunk_digests_json = None
        fp.chunk_digest_offset = 0
        fp.updated_at = NOW
        db.commit()
        media_id = int(media.id)
    finally:
        db.close()

    body = client.post(
        f"/api/v1/hive/media/{media_id}/empreintes/recalculate",
        headers=csrf(client),
    ).json()
    assert body["scheduled_chunks"] == 1
    assert body["wake_sent"] is True


@pytest.mark.skip(reason="Backfill morceaux désactivé (LOT4+ fetch-grant seul).")
def test_recalculate_stale_claim_scope_only(client, app, settings):
    _enable_hive(client)
    db = app.state.session_factory()
    try:
        from tests.test_hive_sharing_catalog_entry_local import _film_with_file

        _force_manual_share_mode(db)
        target_media, target_item = _film_with_file(db, app, settings, tmdb_id=91014, write_hash=True)
        grant_share_intent(db, target_item.id)
        target_id = int(target_item.id)
        target_fp = db.get(HiveFileFingerprint, target_id)
        target_fp.chunk_digests_json = None
        target_fp.chunk_digest_offset = 0
        target_fp.chunk_digest_claimed_at = NOW - timedelta(hours=1)

        active_media = Media(type="film", title="Hors scope actif", year=2099, tmdb_id=91015)
        db.add(active_media)
        db.flush()
        active_item = MediaItem(media_id=active_media.id, state="missing")
        db.add(active_item)
        db.flush()
        grant_share_intent(db, active_item.id)
        active_id = int(active_item.id)
        db.add(
            HiveFileFingerprint(
                media_item_id=active_id,
                path="/tmp/active-out.mkv",
                size=1024,
                sha256_hex="c" * 64,
                chunk_digests_json=None,
                chunk_digest_offset=0,
                chunk_digest_claimed_at=datetime.now(timezone.utc),
            )
        )

        stale_media = Media(type="film", title="Hors scope périmé", year=2099, tmdb_id=91016)
        db.add(stale_media)
        db.flush()
        stale_item = MediaItem(media_id=stale_media.id, state="missing")
        db.add(stale_item)
        db.flush()
        grant_share_intent(db, stale_item.id)
        stale_id = int(stale_item.id)
        stale_claimed = NOW - timedelta(hours=2)
        db.add(
            HiveFileFingerprint(
                media_item_id=stale_id,
                path="/tmp/stale-out.mkv",
                size=1024,
                sha256_hex="d" * 64,
                chunk_digests_json=None,
                chunk_digest_offset=0,
                chunk_digest_claimed_at=stale_claimed,
            )
        )
        db.commit()
        target_media_id = int(target_media.id)
    finally:
        db.close()

    body = client.post(
        f"/api/v1/hive/media/{target_media_id}/empreintes/recalculate",
        headers=csrf(client),
    ).json()
    assert body["claims_recovered"] == 1
    assert body["scheduled_chunks"] == 1

    db = app.state.session_factory()
    try:
        active_fp = db.get(HiveFileFingerprint, active_id)
        assert active_fp.chunk_digest_claimed_at is not None
        stale_fp = db.get(HiveFileFingerprint, stale_id)
        assert stale_fp.chunk_digest_claimed_at is not None
        target_fp2 = db.get(HiveFileFingerprint, target_id)
        assert target_fp2.chunk_digest_claimed_at is None
    finally:
        db.close()


def test_recalculate_reports_missing_fingerprint_row(client, app, settings):
    """Fichier local sans ligne empreinte : compteur explicite."""
    _enable_hive(client)
    db = app.state.session_factory()
    try:
        _force_manual_share_mode(db)
        layout = storage_layout(db, settings)
        video = layout.movies_dir / "fp-missing.mkv"
        write_sparse_video_file(video, PLAUSIBLE_VIDEO_BYTES)
        media = Media(type="film", title="SansFp", year=2099, tmdb_id=91017)
        db.add(media)
        db.flush()
        item = MediaItem(
            media_id=media.id,
            downloaded=True,
            file_path=str(video),
        )
        db.add(item)
        db.flush()
        grant_share_intent(db, item.id)
        db.commit()
        media_id = int(media.id)
    finally:
        db.close()

    body = client.post(
        f"/api/v1/hive/media/{media_id}/empreintes/recalculate",
        headers=csrf(client),
    ).json()
    assert body["skipped_no_fingerprint"] == 1
    assert body["scheduled_chunks"] == 0
    assert body["wake_sent"] is False


def test_pull_only_scope_cannot_patch(client, app, settings):
    from tests.conftest import login
    from tests.test_hive_t2 import _enable_hive as enable_with_public

    login(client)
    enable_with_public(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"hive_public_address": ""}},
        headers=csrf(client),
    )
    db = app.state.session_factory()
    try:
        from tests.test_hive_sharing_catalog_entry_local import _film_with_file

        media, _item = _film_with_file(db, app, settings, tmdb_id=91006, write_hash=True)
        media_id = int(media.id)
        db.commit()
    finally:
        db.close()

    scope = client.get(f"/api/v1/hive/media/{media_id}/sharing-scope").json()
    assert scope["pull_only"] is True
    r = client.patch(
        f"/api/v1/hive/media/{media_id}/sharing",
        json={"exclude_all": True, "excluded_ruche_ids": []},
        headers=csrf(client),
    )
    assert r.status_code == 422
