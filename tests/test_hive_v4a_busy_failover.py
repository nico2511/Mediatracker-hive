"""V4a : pair Hive occupé → source classée suivante (même empreinte)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from tests.test_hive_t5 import NOW, URL_A, URL_B

from mediatracker.core.pipeline.queue import _apply_status
from mediatracker.db.models import (
    Download,
    DownloadHiveFetch,
    HiveMember,
    HiveOriginReachability,
    HiveRuche,
    Media,
    MediaItem,
    Setting,
)
from mediatracker.hive import download_pipeline as dp
from mediatracker.hive.download_aria2 import (
    HIVE_PEER_BUSY_MESSAGE,
    _ranked_sources_for_ctx,
    active_hive_fetches_for_peer,
    apply_hive_fetch_source,
    current_ranked_source_index,
    hive_failover_after_peer_busy,
    prepare_hive_aria2_unlock,
)
from mediatracker.hive.download_pipeline import (
    clear_hive_peer_busy_strike,
    defer_hive_peer_busy,
    hive_peer_busy_key,
    hive_peer_marked_busy,
    mark_hive_peer_busy,
)
from mediatracker.hive.peer_rate_limit import parse_retry_after_header
from mediatracker.providers.base import DownloadStatus

pytest_plugins = ["tests.test_hive_t5"]

SHA = "f" * 64


def _two_source_json(sha: str = SHA) -> str:
    sources = [
        {
            "ruche_id_hex": "01" * 16,
            "origine": "aa",
            "file_id_hex": "a1" * 16,
            "sha256": sha,
            "joignable": True,
        },
        {
            "ruche_id_hex": "01" * 16,
            "origine": "bb",
            "file_id_hex": "b2" * 16,
            "sha256": sha,
            "joignable": True,
        },
    ]
    return json.dumps(sources, separators=(",", ":"))


def _seed_pending_hive_row(db, *, sources_json: str, peer_url: str = URL_A, active_index: int = 0):
    now = datetime.now(timezone.utc)
    media = Media(type="film", title="V4a Demo", year=2020, tmdb_id=9100, followed=True)
    db.add(media)
    db.flush()
    item = MediaItem(media_id=media.id, state="pending")
    db.add(item)
    db.flush()
    row = Download(media_item_id=item.id, state="pending", created_at=now)
    db.add(row)
    db.flush()
    rid = bytes.fromhex("01" * 16)
    db.merge(HiveRuche(ruche_id=rid, creator_instance_id="cr1"))
    db.flush()
    for origin, url, bps in (("aa", URL_A, 900.0), ("bb", URL_B, 100.0)):
        db.merge(
            HiveMember(
                ruche_id=rid,
                member_instance_id=origin,
                public_address=url,
                joined_at=now,
            )
        )
        db.merge(
            HiveOriginReachability(
                ruche_id=rid,
                origin_instance_id=origin,
                last_ok_at=now,
                measured_bps=bps,
            )
        )
    db.add(
        DownloadHiveFetch(
            download_id=row.id,
            sha256_hex=SHA,
            sources_json=sources_json,
            active_source_index=active_index,
            ruche_id=rid,
            file_id=os.urandom(16),
            origin_instance_id="aa",
            peer_base_url=peer_url,
            expected_size=100,
            created_at=now,
            updated_at=now,
        )
    )
    db.commit()
    return row, item, media


def test_parse_retry_after_header_respects_seconds():
    assert parse_retry_after_header("45") == 45.0
    assert parse_retry_after_header(None) is None


def test_failover_single_source_stays_deferred(client):
    db = client.app.state.session_factory()
    try:
        row, _, _ = _seed_pending_hive_row(db, sources_json=_two_source_json())
        ctx = db.get(DownloadHiveFetch, row.id)
        assert ctx is not None
        ctx.sources_json = json.dumps([json.loads(_two_source_json())[0]], separators=(",", ":"))
        db.commit()
        now = datetime.now(timezone.utc)
        assert hive_failover_after_peer_busy(db, row, ctx, now) is False
        defer_hive_peer_busy(row, now)
        assert row.error_code == "HIVE_PEER_BUSY"
        assert row.next_retry_at is not None
    finally:
        db.close()


def test_failover_two_sources_skips_busy_peer(client):
    db = client.app.state.session_factory()
    settings = client.app.state.settings
    now = datetime.now(timezone.utc)
    try:
        db.merge(Setting(key="hive_transfer_parallel", value="1", is_secret=False, updated_at=NOW))
        busy_media = Media(type="film", title="Busy Slot", year=2020, tmdb_id=9101, followed=True)
        db.add(busy_media)
        db.flush()
        busy_item = MediaItem(media_id=busy_media.id, state="downloading")
        db.add(busy_item)
        db.flush()
        busy_row = Download(media_item_id=busy_item.id, state="downloading", created_at=now)
        db.add(busy_row)
        db.flush()
        db.add(
            DownloadHiveFetch(
                download_id=busy_row.id,
                sha256_hex="e" * 64,
                sources_json="[]",
                active_source_index=0,
                ruche_id=bytes.fromhex("01" * 16),
                file_id=os.urandom(16),
                origin_instance_id="aa",
                peer_base_url=URL_A,
                expected_size=50,
                created_at=now,
                updated_at=now,
            )
        )
        row, item, media = _seed_pending_hive_row(db, sources_json=_two_source_json())
        ctx = db.get(DownloadHiveFetch, row.id)
        assert ctx is not None
        clear_hive_peer_busy_strike(row.id)
        assert active_hive_fetches_for_peer(db, URL_A, exclude_download_id=row.id) >= 1
        match = MagicMock()
        match.sha256_hex = SHA
        match.expected_size = 100
        match.sources = json.loads(_two_source_json())
        match.cle = {"nom": "piece.mkv"}
        monkey = pytest.MonkeyPatch()
        monkey.setattr(
            "mediatracker.hive.source_download.resolve_hive_match_for_item",
            lambda *a, **k: MagicMock(outcome="queued", match=match),
        )
        monkey.setattr(
            "mediatracker.hive.download_aria2.mint_remote_fetch_grant",
            lambda *a, **k: __import__(
                "mediatracker.hive.fetch_grant", fromlist=["MintedFetchGrant"]
            ).MintedFetchGrant(
                grant_secret="opaque-test-grant-value-0123456789",
                expires_at=now + timedelta(hours=1),
            ),
        )
        try:
            outcome = prepare_hive_aria2_unlock(
                db,
                settings,
                row=row,
                item=item,
                media=media,
                now=now,
                defer_final=False,
            )
            assert outcome is not None and outcome != "waiting"
            assert ctx.origin_instance_id == "bb"
            assert ctx.peer_base_url == URL_B
            assert row.error_code is None
        finally:
            monkey.undo()
    finally:
        db.close()


def test_failover_all_sources_busy_defers_without_tight_loop(client, monkeypatch):
    db = client.app.state.session_factory()
    settings = client.app.state.settings
    now = datetime.now(timezone.utc)
    try:
        db.merge(Setting(key="hive_transfer_parallel", value="1", is_secret=False, updated_at=NOW))
        for i, url in enumerate((URL_A, URL_B)):
            media = Media(type="film", title=f"Occ {i}", year=2020, tmdb_id=9200 + i, followed=True)
            db.add(media)
            db.flush()
            item = MediaItem(media_id=media.id, state="downloading")
            db.add(item)
            db.flush()
            row = Download(media_item_id=item.id, state="downloading", created_at=now)
            db.add(row)
            db.flush()
            db.add(
                DownloadHiveFetch(
                    download_id=row.id,
                    sha256_hex=f"{i:064x}",
                    sources_json="[]",
                    active_source_index=0,
                    ruche_id=bytes.fromhex("01" * 16),
                    file_id=os.urandom(16),
                    origin_instance_id="aa",
                    peer_base_url=url,
                    expected_size=50,
                    created_at=now,
                    updated_at=now,
                )
            )
        target, item, media = _seed_pending_hive_row(db, sources_json=_two_source_json())
        match = MagicMock()
        match.sha256_hex = SHA
        match.expected_size = 100
        match.sources = json.loads(_two_source_json())
        match.cle = {"nom": "piece.mkv"}
        mint_calls = {"n": 0}

        def _no_mint(*_a, **_k):
            mint_calls["n"] += 1
            raise AssertionError("mint interdit quand tous les pairs sont occupés")

        monkeypatch.setattr(
            "mediatracker.hive.source_download.resolve_hive_match_for_item",
            lambda *a, **k: MagicMock(outcome="queued", match=match),
        )
        monkeypatch.setattr("mediatracker.hive.download_aria2.mint_remote_fetch_grant", _no_mint)
        outcome = prepare_hive_aria2_unlock(
            db,
            settings,
            row=target,
            item=item,
            media=media,
            now=now,
            defer_final=False,
        )
        assert outcome == "waiting"
        assert target.error_code == "HIVE_PEER_BUSY"
        assert target.error == HIVE_PEER_BUSY_MESSAGE
        assert target.next_retry_at is not None
        assert mint_calls["n"] == 0
        delay = (target.next_retry_at - now).total_seconds()
        assert delay >= 25
    finally:
        db.close()


def test_marked_busy_peer_skipped_until_expiry(client):
    db = client.app.state.session_factory()
    now = datetime.now(timezone.utc)
    try:
        row, _, _ = _seed_pending_hive_row(db, sources_json=_two_source_json())
        clear_hive_peer_busy_strike(row.id)
        key_a = hive_peer_busy_key(URL_A)
        mark_hive_peer_busy(row.id, key_a, now, retry_after=timedelta(seconds=30))
        assert hive_peer_marked_busy(row.id, key_a, now)
        later = now + timedelta(seconds=31)
        assert not hive_peer_marked_busy(row.id, key_a, later)
    finally:
        db.close()


def test_marked_busy_peer_not_revisited_during_failover_scan(client):
    db = client.app.state.session_factory()
    now = datetime.now(timezone.utc)
    try:
        row, _, _ = _seed_pending_hive_row(db, sources_json=_two_source_json())
        ctx = db.get(DownloadHiveFetch, row.id)
        assert ctx is not None
        clear_hive_peer_busy_strike(row.id)
        mark_hive_peer_busy(row.id, hive_peer_busy_key(URL_A), now, retry_after=timedelta(minutes=2))
        ranked = json.loads(_two_source_json())
        assert apply_hive_fetch_source(db, ctx, ranked[0], index=0, now=now)
        assert hive_failover_after_peer_busy(db, row, ctx, now)
        assert ctx.peer_base_url == URL_B
    finally:
        db.close()


def test_failover_ring_can_return_to_source_after_peer_mark_expires(client):
    db = client.app.state.session_factory()
    now = datetime.now(timezone.utc)
    try:
        row, _, _ = _seed_pending_hive_row(db, sources_json=_two_source_json())
        ctx = db.get(DownloadHiveFetch, row.id)
        assert ctx is not None
        clear_hive_peer_busy_strike(row.id)
        ranked = _ranked_sources_for_ctx(db, ctx)
        idx_b = next(i for i, s in enumerate(ranked) if s["origine"] == "bb")
        apply_hive_fetch_source(db, ctx, ranked[idx_b], index=idx_b, now=now)
        assert ctx.origin_instance_id == "bb"
        mark_hive_peer_busy(row.id, hive_peer_busy_key(URL_B), now, retry_after=timedelta(minutes=5))
        mark_hive_peer_busy(row.id, hive_peer_busy_key(URL_A), now, retry_after=timedelta(seconds=1))
        later = now + timedelta(seconds=2)
        assert not hive_peer_marked_busy(row.id, hive_peer_busy_key(URL_A), later)
        assert hive_failover_after_peer_busy(db, row, ctx, later)
        assert ctx.origin_instance_id == "aa"
        assert ctx.peer_base_url == URL_A
    finally:
        db.close()


def test_current_source_index_tracks_identity_after_rank_inversion(client):
    db = client.app.state.session_factory()
    now = datetime.now(timezone.utc)
    try:
        row, _, _ = _seed_pending_hive_row(db, sources_json=_two_source_json())
        ctx = db.get(DownloadHiveFetch, row.id)
        assert ctx is not None
        ctx.active_source_index = 99
        rid = bytes.fromhex("01" * 16)
        db.merge(HiveOriginReachability(ruche_id=rid, origin_instance_id="aa", last_ok_at=now, measured_bps=10.0))
        db.merge(HiveOriginReachability(ruche_id=rid, origin_instance_id="bb", last_ok_at=now, measured_bps=9000.0))
        db.flush()
        ranked = _ranked_sources_for_ctx(db, ctx)
        assert ranked[0]["origine"] == "bb"
        idx = current_ranked_source_index(ranked, ctx)
        assert ranked[idx]["origine"] == "aa"
    finally:
        db.close()


def test_failover_all_busy_restores_ctx(client):
    db = client.app.state.session_factory()
    now = datetime.now(timezone.utc)
    try:
        row, _, _ = _seed_pending_hive_row(db, sources_json=_two_source_json())
        ctx = db.get(DownloadHiveFetch, row.id)
        assert ctx is not None
        clear_hive_peer_busy_strike(row.id)
        before_origin = ctx.origin_instance_id
        before_peer = ctx.peer_base_url
        before_file = bytes(ctx.file_id)
        ctx.grant_ciphertext = "opaque-grant-ciphertext"
        ctx.grant_expires_at = now + timedelta(hours=1)
        mark_hive_peer_busy(row.id, hive_peer_busy_key(URL_A), now, retry_after=timedelta(minutes=5))
        mark_hive_peer_busy(row.id, hive_peer_busy_key(URL_B), now, retry_after=timedelta(minutes=5))
        assert hive_failover_after_peer_busy(db, row, ctx, now) is False
        assert ctx.origin_instance_id == before_origin
        assert ctx.peer_base_url == before_peer
        assert ctx.file_id == before_file
        assert ctx.grant_ciphertext == "opaque-grant-ciphertext"
    finally:
        db.close()


def test_prepare_keeps_current_source_when_both_free(client, monkeypatch):
    db = client.app.state.session_factory()
    settings = client.app.state.settings
    now = datetime.now(timezone.utc)
    try:
        row, item, media = _seed_pending_hive_row(db, sources_json=_two_source_json())
        ctx = db.get(DownloadHiveFetch, row.id)
        assert ctx is not None
        assert ctx.origin_instance_id == "aa"
        clear_hive_peer_busy_strike(row.id)
        match = MagicMock()
        match.sha256_hex = SHA
        match.expected_size = 100
        match.sources = json.loads(_two_source_json())
        match.cle = {"nom": "piece.mkv"}
        mint_origins: list[str] = []
        grant = __import__("mediatracker.hive.fetch_grant", fromlist=["MintedFetchGrant"]).MintedFetchGrant(
            grant_secret="opaque-test-grant-value-0123456789",
            expires_at=now + timedelta(hours=1),
        )

        def _track_mint(db, settings, hive_ctx, *, now: datetime, force: bool = False):
            del db, settings, now, force
            mint_origins.append(hive_ctx.origin_instance_id)
            return grant

        monkeypatch.setattr(
            "mediatracker.hive.source_download.resolve_hive_match_for_item",
            lambda *a, **k: MagicMock(outcome="queued", match=match),
        )
        monkeypatch.setattr("mediatracker.hive.download_aria2.ensure_mint_for_download", _track_mint)
        outcome = prepare_hive_aria2_unlock(
            db,
            settings,
            row=row,
            item=item,
            media=media,
            now=now,
            defer_final=False,
        )
        assert outcome is not None and outcome != "waiting"
        assert ctx.origin_instance_id == "aa"
        assert ctx.peer_base_url == URL_A
        assert mint_origins == ["aa"]
        assert dp._hive_failover_switches.get(row.id, 0) == 0
    finally:
        db.close()


def test_prepare_b_429_returns_to_a_when_a_unmarked(client, monkeypatch):
    db = client.app.state.session_factory()
    settings = client.app.state.settings
    now = datetime.now(timezone.utc)
    try:
        row, item, media = _seed_pending_hive_row(db, sources_json=_two_source_json())
        ctx = db.get(DownloadHiveFetch, row.id)
        assert ctx is not None
        ranked = _ranked_sources_for_ctx(db, ctx)
        idx_b = next(i for i, s in enumerate(ranked) if s["origine"] == "bb")
        apply_hive_fetch_source(db, ctx, ranked[idx_b], index=idx_b, now=now)
        assert ctx.origin_instance_id == "bb"
        clear_hive_peer_busy_strike(row.id)
        match = MagicMock()
        match.sha256_hex = SHA
        match.expected_size = 100
        match.sources = json.loads(_two_source_json())
        match.cle = {"nom": "piece.mkv"}
        grant = __import__("mediatracker.hive.fetch_grant", fromlist=["MintedFetchGrant"]).MintedFetchGrant(
            grant_secret="opaque-test-grant-value-0123456789",
            expires_at=now + timedelta(hours=1),
        )

        def _mint_or_rate(db, settings, hive_ctx, *, now: datetime, force: bool = False):
            del db, settings, force
            if hive_ctx.origin_instance_id == "bb":
                raise __import__(
                    "mediatracker.hive.download_aria2", fromlist=["HiveGrantMintError"]
                ).HiveGrantMintError("peer_rate", "occupé")
            return grant

        monkeypatch.setattr(
            "mediatracker.hive.source_download.resolve_hive_match_for_item",
            lambda *a, **k: MagicMock(outcome="queued", match=match),
        )
        monkeypatch.setattr("mediatracker.hive.download_aria2.ensure_mint_for_download", _mint_or_rate)
        outcome = prepare_hive_aria2_unlock(
            db,
            settings,
            row=row,
            item=item,
            media=media,
            now=now,
            defer_final=False,
        )
        assert outcome is not None and outcome != "waiting"
        assert ctx.origin_instance_id == "aa"
        assert ctx.peer_base_url == URL_A
    finally:
        db.close()


def test_apply_status_429_fails_over_immediate_retry(client, monkeypatch):
    from tests.test_hive_aria2_source_unreachable import _TrackingDownloader

    db = client.app.state.session_factory()
    settings = client.app.state.settings
    now = datetime.now(timezone.utc)
    try:
        row, _, _ = _seed_pending_hive_row(db, sources_json=_two_source_json())
        row.state = "downloading"
        row.handle = "gid-test"
        ctx = db.get(DownloadHiveFetch, row.id)
        assert ctx is not None
        db.commit()
        def _failover(db, r, c, moment, **kwargs):
            c.active_source_index = 1
            c.origin_instance_id = "bb"
            c.peer_base_url = URL_B
            db.flush()
            return True

        monkeypatch.setattr(
            "mediatracker.hive.download_aria2.hive_failover_after_peer_busy",
            _failover,
        )
        downloader = _TrackingDownloader()
        status = DownloadStatus(
            state="error",
            error="status=429 Too Many Requests",
            done_bytes=10,
            total_bytes=100,
            speed=None,
            path="/data/incomplete/demo.mkv",
        )
        _apply_status(db, row, status, now, downloader, settings=settings)
        assert row.state == "pending"
        assert row.error_code is None
        assert row.next_retry_at is not None
        assert row.next_retry_at <= now + timedelta(seconds=1)
        assert downloader.delete_flags == [False]
        db.expire_all()
        ctx = db.get(DownloadHiveFetch, row.id)
        assert ctx is not None
        assert ctx.active_source_index == 1
    finally:
        db.close()