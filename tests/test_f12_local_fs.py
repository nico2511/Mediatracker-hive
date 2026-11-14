import pytest

from mediatracker.core import local_fs


@pytest.fixture(autouse=True)
def _clear_mount_cache():
    local_fs.clear_local_fs_cache()
    yield
    local_fs.clear_local_fs_cache()


def test_is_local_filesystem_uses_mountinfo(monkeypatch, tmp_path):
    root = tmp_path / "lib"
    root.mkdir()
    table = (
        ("/", "ext4"),
        (str(tmp_path), "ext4"),
    )
    monkeypatch.setattr(local_fs, "_mount_table", lambda: table)
    local_fs.clear_local_fs_cache()
    assert local_fs.is_local_filesystem(root) is True


def test_network_and_fuse_are_not_local(monkeypatch, tmp_path):
    root = tmp_path / "nas"
    root.mkdir()
    monkeypatch.setattr(
        local_fs,
        "_mount_table",
        lambda: ((str(tmp_path), "nfs4"),),
    )
    local_fs.clear_local_fs_cache()
    assert local_fs.is_local_filesystem(root) is False
    monkeypatch.setattr(
        local_fs,
        "_mount_table",
        lambda: ((str(tmp_path), "fuse.sshfs"),),
    )
    local_fs.clear_local_fs_cache()
    assert local_fs.is_local_filesystem(root) is False


def test_unknown_fstype_is_not_local(monkeypatch, tmp_path):
    root = tmp_path / "weird"
    root.mkdir()
    monkeypatch.setattr(
        local_fs,
        "_mount_table",
        lambda: ((str(tmp_path), "orangefs"),),
    )
    local_fs.clear_local_fs_cache()
    assert local_fs.is_local_filesystem(root) is False


def test_mountinfo_result_is_cached(monkeypatch, tmp_path):
    root = tmp_path / "cached"
    root.mkdir()
    calls = {"n": 0}

    def table():
        calls["n"] += 1
        return ((str(tmp_path), "ext4"),)

    monkeypatch.setattr(local_fs, "_mount_table", table)
    local_fs.clear_local_fs_cache()
    assert local_fs.is_local_filesystem(root) is True
    assert local_fs.is_local_filesystem(root) is True
    assert calls["n"] == 1
