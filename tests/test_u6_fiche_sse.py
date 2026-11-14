"""U6: fiche view listens to the shared progress SSE stream."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DETAIL = ROOT / "frontend" / "src" / "views" / "MediaDetailView.vue"
DOWNLOADS = ROOT / "frontend" / "src" / "views" / "DownloadsView.vue"
COMPOSABLE = ROOT / "frontend" / "src" / "composables" / "useProgressStream.ts"


def test_downloads_and_fiche_use_shared_progress_stream():
    downloads = DOWNLOADS.read_text(encoding="utf-8")
    detail = DETAIL.read_text(encoding="utf-8")
    composable = COMPOSABLE.read_text(encoding="utf-8")
    assert "useProgressStream" in composable
    assert "EventSource" in composable
    assert "media_id=" in composable
    assert "useProgressStream" in downloads
    assert "useProgressStream" in detail
    assert "applyFicheStream" in detail
    assert "fiches" in detail
