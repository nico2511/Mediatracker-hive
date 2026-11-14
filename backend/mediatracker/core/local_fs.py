"""Detect whether a path sits on a local block filesystem (Linux mountinfo)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

_LOCAL_FSTYPES = frozenset(
    {
        "ext2",
        "ext3",
        "ext4",
        "xfs",
        "btrfs",
        "zfs",
        "f2fs",
        "reiserfs",
        "jfs",
        "nilfs2",
        "tmpfs",
    }
)
_NETWORK_FSTYPES = frozenset(
    {
        "nfs",
        "nfs4",
        "cifs",
        "smb",
        "smb3",
        "ceph",
        "glusterfs",
        "afs",
        "ncpfs",
        "ncp",
        "sshfs",
        "curlftpfs",
        "ftpfs",
        "davfs",
        "fuse.sshfs",
    }
)


def clear_local_fs_cache() -> None:
    """Tests may call this after patching mountinfo."""
    is_local_filesystem.cache_clear()
    table_loader = globals().get("_mount_table")
    if hasattr(table_loader, "cache_clear"):
        table_loader.cache_clear()


@lru_cache(maxsize=256)
def is_local_filesystem(path: str | Path) -> bool:
    """True when ``path`` is on a known local disk filesystem.

    Network mounts, FUSE remotes, and unknown types are treated as non-local.
    """
    try:
        resolved = os.path.realpath(str(path))
    except OSError:
        return False
    mount_point, fstype = _mount_for_path(resolved)
    if mount_point is None or fstype is None:
        return False
    return _classify_fstype(fstype)


def _classify_fstype(fstype: str) -> bool:
    folded = fstype.lower()
    if folded in _LOCAL_FSTYPES:
        return True
    if folded in _NETWORK_FSTYPES:
        return False
    if folded.startswith("fuse."):
        return False
    if folded == "fuse":
        return False
    return False


def _mount_for_path(resolved: str) -> tuple[str | None, str | None]:
    best_point: str | None = None
    best_fstype: str | None = None
    best_len = -1
    for mount_point, fstype in _mount_table():
        if resolved == mount_point or resolved.startswith(mount_point.rstrip("/") + "/"):
            length = len(mount_point)
            if length > best_len:
                best_len = length
                best_point = mount_point
                best_fstype = fstype
    return best_point, best_fstype


@lru_cache(maxsize=1)
def _mount_table() -> tuple[tuple[str, str], ...]:
    mountinfo = Path("/proc/self/mountinfo")
    if not mountinfo.is_file():
        return ()
    rows: list[tuple[str, str]] = []
    for line in mountinfo.read_text(encoding="utf-8", errors="replace").splitlines():
        if " - " not in line:
            continue
        before, after = line.split(" - ", 1)
        parts = before.split()
        if len(parts) < 5:
            continue
        mount_point = parts[4].replace("\\040", " ")
        fstype = after.split()[0] if after.split() else ""
        if not fstype:
            continue
        rows.append((mount_point, fstype))
    return tuple(rows)
