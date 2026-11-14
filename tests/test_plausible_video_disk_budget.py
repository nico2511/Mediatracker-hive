"""Budget disque des fixtures vidéo réalistes (régression CI saturation)."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import (
    PLAUSIBLE_VIDEO_BYTES,
    sparse_payload_alloc_bytes,
    write_sparse_video_file,
)


def test_plausible_video_sparse_logical_size_matches_stat(tmp_path: Path) -> None:
    path = tmp_path / "fictive.mkv"
    write_sparse_video_file(path, PLAUSIBLE_VIDEO_BYTES)
    assert path.stat().st_size == PLAUSIBLE_VIDEO_BYTES


def test_plausible_video_sparse_on_disk_footprint_is_bounded(tmp_path: Path) -> None:
    path = tmp_path / "fictive.mkv"
    write_sparse_video_file(path, PLAUSIBLE_VIDEO_BYTES)
    on_disk = sparse_payload_alloc_bytes(path)
    # CI runners ~14 GiB libres : une vidéo pleine 800 Mio ne doit pas occuper des centaines de Mio.
    assert on_disk < 512 * 1024
