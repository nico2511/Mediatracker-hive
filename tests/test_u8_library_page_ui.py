"""U8: Bibliothèque centrée disque, onglets et sections repliables."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / "frontend" / "src" / "views" / "LibraryView.vue"
CSS = ROOT / "frontend" / "src" / "styles.css"


def test_library_view_disk_tabs_and_sections():
    source = VIEW.read_text(encoding="utf-8")
    assert "mediatracker.library.sections.v2" in source
    assert "library-tabs" in source
    assert 'query.onglet' in source or "onglet" in source
    assert "Fichiers sur le disque uniquement" in source
    assert "Films à ranger" in source
    assert "Séries à ranger" in source
    assert "Films OK" in source
    assert "Séries OK" in source
    assert "Importer l'historique" not in source
    assert "library-calm-grid" not in source
    assert "PosterCard" not in source
    assert "DeleteFicheDialog" not in source
    assert "Relire les incomplets" in source
    assert 'data-align-step="align"' in source
    assert ">Aligner<" in source
    assert ">Ranger<" in source
    assert 'data-align-step="done"' in source
    assert "Terminer l'alignement" in source
    assert "library-align-flow" in source


def test_library_section_styles_in_css():
    css = CSS.read_text(encoding="utf-8")
    assert ".library-section-toggle" in css
    assert '.library-section-toggle[aria-expanded="true"]' in css
    assert ".library-tabs" in css
