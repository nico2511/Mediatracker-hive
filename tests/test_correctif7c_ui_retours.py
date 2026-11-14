"""Correctif 7c — structure UI (grilles, accueil épisodes, fiche saison)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / "frontend" / "src" / "views" / "HomeView.vue"
MEDIA = ROOT / "frontend" / "src" / "views" / "MediaListView.vue"
DETAIL = ROOT / "frontend" / "src" / "views" / "MediaDetailView.vue"
CSS = ROOT / "frontend" / "src" / "styles.css"


def test_upcoming_week_uses_portrait_poster_grid():
    source = HOME.read_text(encoding="utf-8")
    block = source.split('title="À venir"', 1)[1].split("HomeGridSection", 1)[0]
    assert "home-episode-grid" not in block
    assert "collapsed-desktop" not in block
    assert "collapsed-mobile" not in block
    assert "PosterCard" in block
    assert "pc-grid pc-grid--home" in block


def test_media_library_toolbar_has_spacing_before_grid():
    css = CSS.read_text(encoding="utf-8")
    assert ".media-library-toolbar" in css
    toolbar = css.split(".media-library-toolbar {", 1)[1].split("}", 1)[0]
    assert "margin-bottom" in toolbar
    pc = css.split(".poster-grid.pc-grid {", 1)[1].split("}", 1)[0]
    assert "minmax(var(--poster-card-w), 1fr)" in pc


def test_fiche_season_watch_notice_and_intro_panel():
    source = DETAIL.read_text(encoding="utf-8")
    assert "fiche-seasons-intro" in source
    assert "data-season-watch-notice" in source
    assert "markSeasonWatched" in source
    assert "seasonNotice" in source
