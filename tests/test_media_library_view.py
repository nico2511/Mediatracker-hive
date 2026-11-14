"""Médias page uses the Vidéothèque grid layout and density preference."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / "frontend" / "src" / "views" / "MediaListView.vue"
CSS = ROOT / "frontend" / "src" / "styles.css"


def test_media_list_view_wires_videotheque_grid_and_density_storage():
    source = VIEW.read_text(encoding="utf-8")
    assert "PosterCard" in source
    assert "mediatracker.medias.density.v2" in source
    assert 'class="poster-grid pc-grid media-library-grid"' in source
    assert "pc-grid--dense" in source
    assert "PosterCard" in source
    assert '@open="quick = mediaTarget(item)"' in source
    assert "@click=\"unfollowIdentified\"" in source
    assert 'placeholder="Rechercher dans la collection"' in source
    assert "media-library-extra-filters" in source
    assert "Chargement…" in source
    assert "Fin de liste ·" in source
    assert "reloadGeneration" in source or "++reloadGeneration" in source
    assert "fetchAllMedia" in source
    assert "Densité" in source and "Aérée" in source and "Dense" in source


def test_media_library_grid_columns_in_css():
    css = CSS.read_text(encoding="utf-8")
    assert "--poster-card-w: 9.5rem" in css
    assert ".poster-grid.pc-grid" in css
    assert "@media (hover: none), (max-width: 720px)" in css
    mobile = css.split("@media (max-width: 720px)", 1)[1]
    assert "repeat(3, minmax(0, 1fr))" in mobile
