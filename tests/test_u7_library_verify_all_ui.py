"""U7: Bibliothèque exposes verify-all controls wired to the R2 API."""

from pathlib import Path


def test_library_view_wires_verify_all_endpoint():
    source = (Path(__file__).resolve().parents[1] / "frontend" / "src" / "views" / "LibraryView.vue").read_text(
        encoding="utf-8"
    )
    assert 'data-verify-all-start' in source
    assert "Tout vérifier" in source
    assert "/api/v1/library/verify-all" in (
        Path(__file__).resolve().parents[1] / "frontend" / "src" / "composables" / "useLibraryVerifyAll.ts"
    ).read_text(encoding="utf-8")
    assert "useLibraryVerifyAll" in source
    assert "Relancer le scan" in source
