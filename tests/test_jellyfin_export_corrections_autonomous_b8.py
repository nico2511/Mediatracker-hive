"""B8: correction CSV export must work without app startup wiring (fresh process)."""

from __future__ import annotations

from mediatracker.integrations.jellyfin_db_export import ExportBundle, ExportStats, correction_csv_rows


def test_correction_csv_empty_bundle_without_tmdb_wiring():
    """Reproduces B8: empty bundle must return [] without wire_jellyfin_integrations."""
    bundle = ExportBundle(rows=[], stats=ExportStats(), csv_rows=[])
    assert correction_csv_rows(bundle) == []
