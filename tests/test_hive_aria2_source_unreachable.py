"""Erreurs aria2 transitoires sur téléchargement Hive (pas de logique CDN AllDebrid)."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from tests.test_hive_t5 import URL_A

from mediatracker.core.pipeline.queue import _apply_status
from mediatracker.db.models import Download, DownloadHiveFetch, Media, MediaItem
from mediatracker.providers.base import DownloadStatus

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)


class _TrackingDownloader:
    def __init__(self) -> None:
        self.delete_flags: list[bool] = []

    def remove(self, handle: str, *, delete_files: bool = False) -> None:
        del handle
        self.delete_flags.append(delete_files)


def _seed_hive_download(db, *, title: str = "Fiction Alpha") -> Download:
    media = Media(type="film", title=title, year=2020, tmdb_id=8801, followed=True)
    db.add(media)
    db.flush()
    item = MediaItem(media_id=media.id, state="downloading")
    db.add(item)
    db.flush()
    row = Download(
        media_item_id=item.id,
        state="queued",
        handle="hive-gid-1",
        progress=10,
        created_at=NOW,
    )
    db.add(row)
    db.flush()
    db.add(
        DownloadHiveFetch(
            download_id=row.id,
            sha256_hex="d" * 64,
            sources_json="[]",
            active_source_index=0,
            ruche_id=os.urandom(16),
            file_id=os.urandom(16),
            origin_instance_id="aa",
            peer_base_url=URL_A,
            expected_size=1024,
            content_sha256_hex="d" * 64,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    db.commit()
    return row


def test_hive_aria2_502_keeps_partial_file(client):
    db = client.app.state.session_factory()
    settings = client.app.state.settings
    try:
        row = _seed_hive_download(db)
        downloader = _TrackingDownloader()
        status = DownloadStatus(
            state="error",
            error="aria2 a échoué : HTTP 502 Bad Gateway",
            done_bytes=100,
            total_bytes=1024,
            speed=None,
            path=None,
        )
        _apply_status(db, row, status, NOW, downloader, settings=settings)
        assert row.error_code == "HIVE_SOURCE_UNREACHABLE"
        assert "instance source Hive" in row.error
        assert "502" in row.error
        assert "conservé" in row.error.casefold()
        assert row.state == "pending"
        assert row.next_retry_at is not None
        assert downloader.delete_flags == [False]
    finally:
        db.close()


def test_alldebrid_aria2_502_still_uses_cdn_path(client):
    db = client.app.state.session_factory()
    settings = client.app.state.settings
    try:
        media = Media(type="film", title="Fiction Beta", year=2020, tmdb_id=8802, followed=True)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, state="downloading")
        db.add(item)
        db.flush()
        row = Download(
            media_item_id=item.id,
            state="queued",
            handle="cdn-gid-1",
            created_at=NOW,
        )
        db.add(row)
        db.commit()
        downloader = _TrackingDownloader()
        status = DownloadStatus(
            state="error",
            error="HTTP 502 Bad Gateway",
            done_bytes=0,
            total_bytes=100,
            speed=None,
            path=None,
        )
        _apply_status(db, row, status, NOW, downloader, settings=settings)
        assert row.error_code == "ARIA2_CDN"
        assert "débridé" in row.error.casefold()
        assert downloader.delete_flags == [True]
    finally:
        db.close()
