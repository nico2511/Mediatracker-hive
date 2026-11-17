"""Hive T6 : ruche comme source unique (catalogue fusionné, Suivre → transfert)."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

from sqlalchemy import select
from tests.conftest import PLAUSIBLE_VIDEO_BYTES, csrf, login

from mediatracker.db.models import (
    Download,
    DownloadHiveFetch,
    HiveCatalogue,
    HiveEntry,
    HiveMember,
    HiveTransferJob,
    Media,
    MediaItem,
)
from mediatracker.hive.catalog import build_catalog_payload, sign_payload
from mediatracker.hive.hive_catalogue import build_merged_hive_catalogue, refresh_hive_catalogue_store
from mediatracker.hive.identity import hive_public_id_from_public_key, load_private_key
from mediatracker.hive.peer_gossip import ingest_signed_entries
from mediatracker.hive.source_catalog import pick_catalog_match
from mediatracker.hive.source_poll import run_hive_follow_poll

NOW = datetime(2026, 11, 7, 14, 0, tzinfo=timezone.utc)

pytest_plugins = ["tests.test_hive_t5"]


def _seed_catalogue_row(
    db,
    *,
    cle: dict,
    version: str,
    sha: str,
    joignable: bool,
    ruche_hex: str = "aa" * 16,
    taille: int | None = None,
    file_id_hex: str = "bb" * 16,
) -> None:
    from tests.conftest import PLAUSIBLE_VIDEO_BYTES

    if taille is None:
        taille = PLAUSIBLE_VIDEO_BYTES
    from mediatracker.hive.hive_catalogue import _stable_work_key

    fid = str(file_id_hex).strip() or ("bb" * 16)
    sources = [
        {
            "ruche_id_hex": ruche_hex,
            "origine": "hv1test-test-test-test-test-test-test",
            "file_id_hex": fid,
            "sha256": sha,
            "joignable": joignable,
        }
    ]
    db.add(
        HiveCatalogue(
            work_key=_stable_work_key(cle),
            version_key=version,
            cle_json=json.dumps(cle, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            sources_json=json.dumps(sources, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            disponible=joignable,
            refreshed_at=NOW,
        )
    )
    if taille > 0:
        fiche = {"taille": taille, "type": cle.get("type"), "tmdb_id": cle.get("tmdb_id")}
        payload = {"taille": taille, "fiche": fiche}
        db.add(
            HiveEntry(
                ruche_id=bytes.fromhex(ruche_hex),
                origin_instance_id=sources[0]["origine"],
                seq=1,
                cle_json=json.dumps(cle, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                fiche_json=json.dumps(fiche, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                sha256_hex=sha,
                file_id=bytes.fromhex(sources[0]["file_id_hex"]),
                etat="disponible",
                payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            )
        )
    db.commit()


def _enable(client):
    login(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"hive_enabled": True, "hive_activation_name": "ab", "hive_public_address": "https://mtr.exemple.fr"}},
        headers=csrf(client),
    )


def _ruche(client) -> bytes:
    login(client)
    rid = client.post("/api/v1/hive/ruches", headers=csrf(client)).json()["ruche_id_hex"]
    return bytes.fromhex(rid)


def _ingest(
    db,
    settings,
    *,
    ruche_id: bytes,
    tmdb_id: int,
    season: int | None,
    episode: int | None,
    version: str,
    media_type: str = "series",
    sha: str,
    joignable: bool,
    seq: int,
    size: int | None = None,
):
    if size is None:
        size = PLAUSIBLE_VIDEO_BYTES
    pk = load_private_key(settings)
    assert pk
    origin = hive_public_id_from_public_key(pk.public_key())
    cle: dict = {"type": media_type, "tmdb_id": tmdb_id, "version": version}
    fiche: dict = {
        "type": media_type,
        "tmdb_id": tmdb_id,
        "title": "Oeuvre fictive",
        "year": 2099,
    }
    if size > 0:
        fiche["taille"] = size
    if media_type == "series":
        cle["saison"] = season
        cle["episode"] = episode
        fiche["season"] = season
        fiche["episode"] = episode
    payload = build_catalog_payload(
        ruche_id=ruche_id,
        origin_instance_id=origin,
        seq=seq,
        cle=cle,
        fiche=fiche,
        sha256_hex=sha,
        file_id=bytes([seq % 251]) * 16,
        etat="disponible",
        taille=size if size > 0 else None,
    )
    sig = sign_payload(pk, payload)
    accepted = ingest_signed_entries(
        db, ruche_id=ruche_id, items=[{"payload": payload, "signature_b64": sig}], now=NOW
    )
    assert accepted == 1
    if joignable:
        from mediatracker.hive.peer_gossip import record_reachability

        record_reachability(db, ruche_id=ruche_id, origin_id=origin, now=NOW)
        db.flush()


def test_merged_version_dedup_two_ruches_two_sources(client):
    _enable(client)
    db = client.app.state.session_factory()
    settings = client.app.state.settings
    r1 = _ruche(client)
    r2 = _ruche(client)
    try:
        sha = "a" * 64
        common = dict(tmdb_id=8801, season=1, episode=1, version="HD+VFF", sha=sha, joignable=True)
        _ingest(db, settings, ruche_id=r1, seq=1, **common)
        _ingest(db, settings, ruche_id=r2, seq=2, **common)
        db.commit()
        merged = build_merged_hive_catalogue(db, now=NOW)
        versions = [v for work in merged for v in work["versions"] if work["cle"]["tmdb_id"] == 8801]
        assert len(versions) == 1
        assert len(versions[0]["sources"]) == 2
        n = refresh_hive_catalogue_store(db, now=NOW)
        db.commit()
        assert n >= 1
        row = db.scalars(select(HiveCatalogue).where(HiveCatalogue.version_key == "HD+VFF")).first()
        assert row is not None
        sources = json.loads(row.sources_json)
        assert len(sources) == 2
    finally:
        db.close()


def test_follow_only_missing_not_watched(client):
    _enable(client)
    db = client.app.state.session_factory()
    rid = _ruche(client)
    media = Media(
        type="series",
        title="Serie fictive",
        year=2099,
        tmdb_id=8802,
        followed=True,
        new_episode_policy="auto",
        catch_up_pending=True,
        pref_quality="HD",
        pref_language="VFF",
    )
    db.add(media)
    db.flush()
    e1 = MediaItem(media_id=media.id, season=1, episode=1, watched=True, state="missing")
    e2 = MediaItem(media_id=media.id, season=1, episode=2, watched=False, state="missing", air_date=date(2020, 1, 1))
    db.add_all([e1, e2])
    db.commit()
    _seed_catalogue_row(
        db,
        cle={"type": "series", "tmdb_id": 8802, "saison": 1, "episode": 1, "version": "1080p+VFF"},
        version="1080p+VFF",
        sha="b" * 64,
        joignable=True,
        ruche_hex=rid.hex(),
    )
    _seed_catalogue_row(
        db,
        cle={"type": "series", "tmdb_id": 8802, "saison": 1, "episode": 2, "version": "1080p+VFF"},
        version="1080p+VFF",
        sha="c" * 64,
        joignable=True,
        ruche_hex=rid.hex(),
    )
    from tests.test_hive_catalogue_request import _ensure_hive_enqueue_prereqs

    _ensure_hive_enqueue_prereqs(db, rid.hex())
    db.commit()
    report = run_hive_follow_poll(db, media, client.app.state.settings, force=True, now=NOW)
    assert report is not None
    assert report.episodes == 1
    assert report.queued == 1
    assert db.scalar(select(HiveTransferJob).limit(1)) is None
    dl = db.scalar(
        select(Download)
        .join(DownloadHiveFetch, DownloadHiveFetch.download_id == Download.id)
        .where(Download.media_item_id == e2.id, DownloadHiveFetch.sha256_hex == "c" * 64)
        .limit(1)
    )
    assert dl is not None
    db.close()


def test_version_priority_profile_then_fallback(client):
    _enable(client)
    db = client.app.state.session_factory()
    rid = _ruche(client)
    media = Media(type="film", title="Film fictif", year=2099, tmdb_id=8803, pref_quality="HD", pref_language="VFF")
    item = MediaItem(media_id=0, season=None, episode=None, state="missing")
    db.add(media)
    db.flush()
    item.media_id = media.id
    db.add(item)
    db.commit()
    _seed_catalogue_row(
        db,
        cle={"type": "film", "tmdb_id": 8803, "version": "4K+VOSTFR"},
        version="4K+VOSTFR",
        sha="d" * 64,
        joignable=True,
        ruche_hex=rid.hex(),
        file_id_hex="d0" * 16,
    )
    _seed_catalogue_row(
        db,
        cle={"type": "film", "tmdb_id": 8803, "version": "HD+VFF"},
        version="HD+VFF",
        sha="e" * 64,
        joignable=True,
        ruche_hex=rid.hex(),
        file_id_hex="e0" * 16,
    )
    match = pick_catalog_match(
        db,
        media,
        item,
        allow_fallback=True,
        quality_fallback=["HD", "1080p"],
        language_fallback=["VFF", "MULTI"],
        profile=None,
        prefer_available=True,
    )
    assert match is not None
    assert match.sha256_hex == "e" * 64
    db.close()


def test_unavailable_stays_pending_no_transfer(client):
    _enable(client)
    db = client.app.state.session_factory()
    settings = client.app.state.settings
    rid = _ruche(client)
    media = Media(
        type="series",
        title="Serie fictive",
        year=2099,
        tmdb_id=8804,
        followed=True,
        new_episode_policy="auto",
        catch_up_pending=True,
        pref_quality="HD",
        pref_language="VFF",
    )
    db.add(media)
    db.flush()
    item = MediaItem(media_id=media.id, season=1, episode=1, watched=False, state="missing", air_date=date(2020, 1, 1))
    db.add(item)
    db.commit()
    _seed_catalogue_row(
        db,
        cle={"type": "series", "tmdb_id": 8804, "saison": 1, "episode": 1, "version": "1080p+VFF"},
        version="1080p+VFF",
        sha="f" * 64,
        joignable=False,
        ruche_hex=rid.hex(),
    )
    report = run_hive_follow_poll(db, media, settings, force=True, now=NOW)
    assert report is not None
    assert report.queued == 0
    assert report.proposed == 1
    assert db.scalar(select(HiveTransferJob).limit(1)) is None
    db.close()


def test_announce_without_taille_never_enqueues_transfer(client):
    _enable(client)
    db = client.app.state.session_factory()
    settings = client.app.state.settings
    rid = _ruche(client)
    media = Media(
        type="film",
        title="Film fictif",
        year=2099,
        tmdb_id=8810,
        followed=True,
        new_episode_policy="auto",
        catch_up_pending=True,
        pref_quality="HD",
        pref_language="VFF",
    )
    db.add(media)
    db.flush()
    item = MediaItem(media_id=media.id, season=None, episode=None, watched=False, state="missing")
    db.add(item)
    db.commit()
    _ingest(
        db,
        settings,
        ruche_id=rid,
        tmdb_id=8810,
        season=None,
        episode=None,
        version="1080p+VFF",
        media_type="film",
        sha="9" * 64,
        joignable=True,
        seq=9,
        size=0,
    )
    refresh_hive_catalogue_store(db, now=NOW)
    db.commit()
    report = run_hive_follow_poll(db, media, settings, force=True, now=NOW)
    assert report is not None
    assert report.queued == 0
    assert report.proposed >= 1
    assert db.scalar(select(HiveTransferJob).limit(1)) is None
    db.close()


def _seed_film_publish_on_a(app_a, ruche_id: bytes, payload: bytes) -> tuple[str, str, str]:
    """Comme T5 _seed_file_on_a, avec type ``film`` et publication ``publish_available``."""
    from mediatracker.core.storage import storage_layout
    from mediatracker.db.models import HiveFileFingerprint
    from mediatracker.hive.catalog import publish_available
    from mediatracker.hive.chunk_digest import file_sha256_hex
    from mediatracker.hive.identity import hive_public_id_from_public_key, load_private_key
    from mediatracker.library.scanner import remember_filed

    settings = app_a.state.settings
    pk = load_private_key(settings)
    assert pk
    origin = hive_public_id_from_public_key(pk.public_key())
    db = app_a.state.session_factory()
    try:
        layout = storage_layout(db, settings)
        folder = layout.movies_dir / "Demo (2020) [tmdbid-9001]"
        folder.mkdir(parents=True, exist_ok=True)
        video = folder / "demo.bin"
        video.write_bytes(payload)
        media = Media(type="film", title="Demo", year=2020, tmdb_id=9001, tmdb_runtime_min=1)
        db.add(media)
        db.flush()
        item = MediaItem(
            media_id=media.id,
            state="downloaded",
            downloaded=True,
            file_path=str(video.resolve()),
            release_quality="1080p",
            release_language="VFF",
        )
        db.add(item)
        db.flush()
        remember_filed(db, video.resolve(), media, item, NOW)
        sha = file_sha256_hex(video)
        db.merge(
            HiveFileFingerprint(
                media_item_id=item.id,
                path=str(video.resolve()),
                size=len(payload),
                mtime_ns=video.stat().st_mtime_ns,
                sha256_hex=sha,
                partial_size=len(payload),
                updated_at=NOW,
            )
        )
        db.commit()
        publish_available(db, settings, ruche_id=ruche_id, media=media, item=item, fingerprint_hex=sha, now=NOW)
        db.flush()
        db.commit()
        entry = db.scalar(
            select(HiveEntry).where(HiveEntry.ruche_id == ruche_id, HiveEntry.origin_instance_id == origin).limit(1)
        )
        assert entry and entry.file_id
        return origin, entry.file_id.hex(), sha
    finally:
        db.close()


def test_signed_publish_follow_enqueues_download(transfer_pair):
    from sqlalchemy import select
    from starlette.testclient import TestClient
    from tests.test_hive_t3_peer_http import _enable as peer_enable
    from tests.test_hive_t5 import URL_A, URL_B, _join_ruche

    from mediatracker.hive.peer_transport import set_peer_asgi_apps_for_tests

    app_a, app_b = transfer_pair
    payload = b"T6" * 500_000
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        peer_enable(ca, "aa", URL_A)
        peer_enable(cb, "bb", URL_B)
        rid = _join_ruche(ca, cb)
        entry = None
        signed: dict = {}
        origin = ""
        sha = ""
        db_a = app_a.state.session_factory()
        try:
            origin, _fid, sha = _seed_film_publish_on_a(app_a, rid, payload)
            entry = db_a.scalar(
                select(HiveEntry)
                .where(HiveEntry.ruche_id == rid, HiveEntry.origin_instance_id == origin)
                .order_by(HiveEntry.id.desc())
                .limit(1)
            )
            assert entry is not None
            signed = json.loads(entry.payload_json or "{}")
            assert signed.get("taille") == len(payload)
            assert json.loads(entry.fiche_json or "{}").get("taille") == len(payload)
        finally:
            db_a.close()

        db_b = app_b.state.session_factory()
        try:
            from mediatracker.hive.peer_gossip import ingest_signed_entries, record_reachability

            assert entry is not None
            assert (
                ingest_signed_entries(
                    db_b,
                    ruche_id=rid,
                    items=[{"payload": signed, "signature_b64": entry.signature_b64}],
                    now=NOW,
                )
                == 1
            )
            record_reachability(db_b, ruche_id=rid, origin_id=origin, now=NOW)
            publisher = db_b.scalar(
                select(HiveMember).where(HiveMember.ruche_id == rid, HiveMember.member_instance_id == origin).limit(1)
            )
            if publisher is None:
                db_b.add(
                    HiveMember(
                        ruche_id=rid,
                        member_instance_id=origin,
                        public_address=URL_A,
                        joined_at=NOW,
                    )
                )
            else:
                publisher.public_address = URL_A
            db_b.flush()
            assert refresh_hive_catalogue_store(db_b, now=NOW) >= 1
            db_b.commit()
            refresh_hive_catalogue_store(db_b, now=NOW)
            db_b.commit()
            pub_cle = signed.get("cle") if isinstance(signed.get("cle"), dict) else {}
            version = str(pub_cle.get("version") or "unknown+unknown")
            quality, language = version.split("+", 1) if "+" in version else ("unknown", "unknown")
            media = Media(
                type="film",
                title="Demo",
                year=2020,
                tmdb_id=9001,
                tmdb_runtime_min=1,
                followed=True,
                new_episode_policy="auto",
                catch_up_pending=True,
                pref_quality=quality,
                pref_language=language,
            )
            db_b.add(media)
            db_b.flush()
            item = MediaItem(
                media_id=media.id,
                state="missing",
                watched=False,
                release_quality=quality,
                release_language=language,
            )
            db_b.add(item)
            db_b.commit()
            match = pick_catalog_match(
                db_b,
                media,
                item,
                allow_fallback=True,
                quality_fallback=[quality, "HD", "1080p"],
                language_fallback=[language, "VFF", "MULTI"],
                profile=None,
                prefer_available=True,
            )
            assert match is not None
            assert match.expected_size == len(payload)
            assert match.row.disponible
            assert any(s.get("joignable") for s in match.sources)
            report = run_hive_follow_poll(db_b, media, app_b.state.settings, force=True, now=NOW)
            assert report is not None and report.queued == 1
            dl = db_b.scalar(select(Download).where(Download.media_item_id == item.id))
            assert dl is not None
            ctx = db_b.get(DownloadHiveFetch, dl.id)
            assert ctx is not None
            assert ctx.sha256_hex == sha
            assert int(ctx.expected_size) == len(payload)
            assert db_b.scalar(select(HiveTransferJob).limit(1)) is None
            set_peer_asgi_apps_for_tests({URL_A: app_a})
        finally:
            db_b.close()
            set_peer_asgi_apps_for_tests(None)


def test_hive_disabled_skips_source_registry(client):
    db = client.app.state.session_factory()
    from mediatracker.core.selection import registered_download_sources
    from mediatracker.hive.source_poll import run_hive_follow_poll

    assert "hive" in registered_download_sources()
    media = Media(type="film", title="Film fictif", tmdb_id=1, followed=True, new_episode_policy="auto")
    db.add(media)
    db.commit()
    assert run_hive_follow_poll(db, media, client.app.state.settings, force=True) is None
    db.close()
