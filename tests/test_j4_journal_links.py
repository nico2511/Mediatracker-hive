"""J4 — liens « Voir dans le journal » depuis fiche, téléchargements et journal URL."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DETAIL = ROOT / "frontend" / "src" / "views" / "MediaDetailView.vue"
DOWNLOADS = ROOT / "frontend" / "src" / "views" / "DownloadsView.vue"
JOURNAL = ROOT / "frontend" / "src" / "views" / "JournalView.vue"


def test_fiche_details_journal_link_uses_shareable_query():
    source = DETAIL.read_text(encoding="utf-8")
    assert "Voir dans le journal" in source
    assert "path: '/journal'" in source
    assert "media_id: String(media.id)" in source


def test_download_rows_link_to_journal_with_download_id():
    source = DOWNLOADS.read_text(encoding="utf-8")
    assert "function journalQuery(job: Job)" in source
    assert 'download_id: String(job.id)' in source
    assert "Voir dans le journal" in source
    assert source.count("journalQuery(job)") >= 4


def test_journal_page_reads_download_id_from_route_query():
    source = JOURNAL.read_text(encoding="utf-8")
    assert "query.download_id" in source
    assert "params.download_id" in source or 'params.set("download_id"' in source
    assert "downloadId" in source
