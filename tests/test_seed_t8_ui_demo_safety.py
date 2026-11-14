"""Garde-fous du seed UI T8 (pas de destruction hors répertoire démo marqué)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SEED = _REPO / "scripts" / "seed_t8_ui_demo.py"


def _load_seed_module():
    spec = importlib.util.spec_from_file_location("seed_t8_ui_demo", _SEED)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["seed_t8_ui_demo"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_reset_refuses_unmarked_existing_db(tmp_path, monkeypatch):
    mod = _load_seed_module()
    witness = tmp_path / "witness"
    witness.mkdir()
    db = witness / "app.db"
    db.write_text("keep-me")
    monkeypatch.setenv("T8_UI_DEMO_DIR", str(witness))
    monkeypatch.setattr(mod, "OUT", witness)
    with pytest.raises(RuntimeError, match="non marqué"):
        mod._reset_demo_dir()
    assert db.read_text() == "keep-me"


def test_reset_allows_marked_demo_dir(tmp_path, monkeypatch):
    mod = _load_seed_module()
    demo = tmp_path / "demo-marked"
    demo.mkdir()
    (demo / mod.DEMO_MARKER_NAME).write_text("t8-ui-demo\n")
    db = demo / "app.db"
    db.write_text("old")
    monkeypatch.setenv("T8_UI_DEMO_DIR", str(demo))
    monkeypatch.setattr(mod, "OUT", demo)
    mod._reset_demo_dir()
    assert not db.exists()


def test_reset_refuses_unmarked_data_dir_only(tmp_path, monkeypatch):
    mod = _load_seed_module()
    witness = tmp_path / "witness-data"
    witness.mkdir()
    (witness / "data").mkdir()
    (witness / "data" / "library.bin").write_bytes(b"keep")
    monkeypatch.setenv("T8_UI_DEMO_DIR", str(witness))
    monkeypatch.setattr(mod, "OUT", witness)
    with pytest.raises(RuntimeError, match="non marqué"):
        mod._reset_demo_dir()
    assert (witness / "data" / "library.bin").read_bytes() == b"keep"
    assert not (witness / mod.DEMO_MARKER_NAME).exists()


def test_reset_refuses_unmarked_config_dir_only(tmp_path, monkeypatch):
    mod = _load_seed_module()
    witness = tmp_path / "witness-cfg"
    witness.mkdir()
    (witness / "config").mkdir()
    (witness / "config" / "settings.json").write_text("{}")
    monkeypatch.setenv("T8_UI_DEMO_DIR", str(witness))
    monkeypatch.setattr(mod, "OUT", witness)
    with pytest.raises(RuntimeError, match="non marqué"):
        mod._reset_demo_dir()
    assert (witness / "config" / "settings.json").read_text() == "{}"
    assert not (witness / mod.DEMO_MARKER_NAME).exists()


def test_reset_allows_empty_unmarked_dir(tmp_path, monkeypatch):
    mod = _load_seed_module()
    empty = tmp_path / "empty-demo"
    empty.mkdir()
    monkeypatch.setenv("T8_UI_DEMO_DIR", str(empty))
    monkeypatch.setattr(mod, "OUT", empty)
    mod._reset_demo_dir()
    assert (empty / mod.DEMO_MARKER_NAME).read_text() == "t8-ui-demo\n"


def test_reset_refuses_home_path(monkeypatch):
    mod = _load_seed_module()
    home = Path.home() / "t8-forbidden-test-never"
    monkeypatch.setenv("T8_UI_DEMO_DIR", str(home))
    monkeypatch.setattr(mod, "OUT", home)
    with pytest.raises(RuntimeError, match="interdit"):
        mod._reset_demo_dir()
