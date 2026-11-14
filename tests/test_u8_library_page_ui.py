"""U8: Bibliothèque sections repliables, grille calme et étapes Jellyfin sans numéro."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / "frontend" / "src" / "views" / "LibraryView.vue"
CSS = ROOT / "frontend" / "src" / "styles.css"


def test_library_view_collapsible_sections_and_calm_grid():
    source = VIEW.read_text(encoding="utf-8")
    assert "mediatracker.library.sections.v2" in source
    assert "aria-expanded" in source
    assert "library-section-toggle" in source
    assert "library-calm-section" in source
    assert 'data-library-section="calm"' in source
    assert "Séries sans problème" in source
    assert "poster-grid pc-grid" in source
    assert "library-head-actions" in source
    assert "Importer l'historique" in source
    assert "library-subnav" not in source
    assert "PosterCard" in source
    assert "data-align-step=\"align\"" in source
    assert "1. Aligner" not in source
    assert ">Aligner<" in source
    assert ">Ranger<" in source
    assert 'data-align-step="done"' in source
    assert "Terminer l'alignement" in source
    assert ">Terminé<" in source
    assert "library-align-flow" in source


def test_library_section_styles_in_css():
    css = CSS.read_text(encoding="utf-8")
    assert ".library-section-toggle" in css
    assert '.library-section-toggle[aria-expanded="true"]' in css
    assert ".library-calm-section" in css
