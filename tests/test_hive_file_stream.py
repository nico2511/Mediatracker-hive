"""GET /hive/files : corps en flux par blocs (pas de lecture intégrale en RAM)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from tests.hive_chunk_digest_testutil import persist_chunk_digests_for_test_file
from tests.test_hive_t3_peer_http import _enable, _settings_pair
from tests.test_hive_t5 import URL_A, _join_ruche, _seed_file_on_a

from mediatracker.api.app import create_app
from mediatracker.db.models import HiveFileFingerprint
from mediatracker.hive.file_serve import HIVE_FILE_SEND_BLOCK, ByteRange, iter_file_byte_range
from mediatracker.hive.identity import load_private_key
from mediatracker.hive.peer_auth import sign_request_headers
from mediatracker.hive.peer_rate_limit import PeerRateLimiter


@pytest.fixture
def pair_apps(tmp_path):
    app_a = create_app(_settings_pair(tmp_path, "stream_a"))
    app_b = create_app(_settings_pair(tmp_path, "stream_b"))
    for app in (app_a, app_b):
        app.state.hive_peer_limiter = PeerRateLimiter(limit=10_000, window_seconds=60.0)
    try:
        yield app_a, app_b
    finally:
        app_a.state.engine.dispose()
        app_b.state.engine.dispose()


def _prepare_digests(app, payload: bytes) -> None:
    db = app.state.session_factory()
    try:
        fp = db.scalar(select(HiveFileFingerprint).where(HiveFileFingerprint.size == len(payload)).limit(1))
        assert fp is not None
        persist_chunk_digests_for_test_file(db, fp)
        db.commit()
    finally:
        db.close()


def _signed_request(client: TestClient, app, rid: bytes, fid_hex: str, *, method: str, range_header: str | None):
    path = f"/hive/files/{fid_hex}"
    pk = load_private_key(app.state.settings)
    headers = sign_request_headers(
        private_key=pk, method=method, path=path, query="", ruche_id=rid, body=b""
    )
    if range_header is not None:
        headers["Range"] = range_header
    return client.request(method, path, headers=headers)


class _SpyFile:
    def __init__(self, handle) -> None:
        self._handle = handle
        self.read_sizes: list[int] = []

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._handle.seek(offset, whence)

    def read(self, size: int = -1) -> bytes:
        if size > 0:
            self.read_sizes.append(size)
        return self._handle.read(size)

    def close(self) -> None:
        self._handle.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def test_iter_file_byte_range_never_reads_more_than_block(tmp_path: Path, monkeypatch):
    span = HIVE_FILE_SEND_BLOCK * 3 + 12345
    payload = os.urandom(span)
    path = tmp_path / "multi.mkv"
    path.write_bytes(payload)
    br = ByteRange(1000, 1000 + span - 1)
    spy_holder: list[_SpyFile] = []
    real_open = Path.open

    def _opening(self, mode="r", *args, **kwargs):
        handle = real_open(self, mode, *args, **kwargs)
        if "b" in mode and self == path:
            spy = _SpyFile(handle)
            spy_holder.append(spy)
            return spy
        return handle

    monkeypatch.setattr(Path, "open", _opening)
    out = b"".join(iter_file_byte_range(path, br, block_size=HIVE_FILE_SEND_BLOCK))

    assert out == payload[br.start : br.end + 1]
    assert spy_holder
    assert max(spy_holder[0].read_sizes) <= HIVE_FILE_SEND_BLOCK
    assert all(size <= HIVE_FILE_SEND_BLOCK for size in spy_holder[0].read_sizes)


def test_get_full_file_200_and_partial_206(pair_apps):
    app_a, app_b = pair_apps
    payload = (b"P" * 500_000) + (b"Q" * 2_200_000)
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        _enable(ca, "aa", URL_A)
        _enable(cb, "bb", URL_A)
        rid = _join_ruche(ca, cb)
        _, fid, sha = _seed_file_on_a(app_a, rid, payload)
        _prepare_digests(app_a, payload)
        full = _signed_request(ca, app_a, rid, fid, method="GET", range_header=None)
        assert full.status_code == 200, full.text
        assert full.content == payload
        assert full.headers.get("Hive-Content-Sha256") == sha
        assert full.headers.get("Content-Length") == str(len(payload))
        assert "Content-Range" not in full.headers

        partial = _signed_request(ca, app_a, rid, fid, method="GET", range_header="bytes=500000-999999")
        assert partial.status_code == 206, partial.text
        assert partial.content == payload[500_000:1_000_000]
        assert partial.headers.get("Content-Range") == f"bytes 500000-999999/{len(payload)}"
        assert partial.headers.get("Content-Length") == "500000"


def test_head_matches_get_headers_no_body(pair_apps):
    app_a, app_b = pair_apps
    payload = b"STREAM" * 8192
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        _enable(ca, "aa", URL_A)
        _enable(cb, "bb", URL_A)
        rid = _join_ruche(ca, cb)
        _, fid, _ = _seed_file_on_a(app_a, rid, payload)
        _prepare_digests(app_a, payload)
        get_resp = _signed_request(ca, app_a, rid, fid, method="GET", range_header="bytes=0-4095")
        head_resp = _signed_request(ca, app_a, rid, fid, method="HEAD", range_header="bytes=0-4095")
        assert get_resp.status_code == 206
        assert head_resp.status_code == 206
        assert head_resp.content == b""
        for key in ("Content-Length", "Content-Range", "Accept-Ranges", "Hive-Content-Sha256"):
            assert head_resp.headers.get(key) == get_resp.headers.get(key)

