"""Migration hive_source_offer_mask (0060) et seq masque (0061)."""

from __future__ import annotations

from alembic import command
from sqlalchemy import create_engine, inspect

from mediatracker.db.migrate import make_config


def test_migration_0060_hive_source_offer_mask_upgrade_downgrade(tmp_path):
    url = f"sqlite:///{tmp_path / 'hive-m0060.db'}"
    cfg = make_config(url)
    command.upgrade(cfg, "0059_source_state_probe")
    command.upgrade(cfg, "0060_hive_source_offer_mask")
    engine = create_engine(url)
    try:
        tables = set(inspect(engine).get_table_names())
        assert "hive_source_offer_mask" in tables
        cols = {c["name"] for c in inspect(engine).get_columns("hive_source_offer_mask")}
        assert {"ruche_id", "origin_instance_id", "file_id", "reason", "masked_at"} <= cols
    finally:
        engine.dispose()
    command.downgrade(cfg, "0059_source_state_probe")
    engine = create_engine(url)
    try:
        assert "hive_source_offer_mask" not in set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_migration_0061_adds_masked_origin_seq(tmp_path):
    url = f"sqlite:///{tmp_path / 'hive-m0061.db'}"
    cfg = make_config(url)
    command.upgrade(cfg, "0061_hive_mask_origin_seq")
    engine = create_engine(url)
    try:
        cols = {c["name"] for c in inspect(engine).get_columns("hive_source_offer_mask")}
        assert "masked_origin_seq" in cols
    finally:
        engine.dispose()
    command.downgrade(cfg, "0060_hive_source_offer_mask")
    engine = create_engine(url)
    try:
        cols = {c["name"] for c in inspect(engine).get_columns("hive_source_offer_mask")}
        assert "masked_origin_seq" not in cols
    finally:
        engine.dispose()
