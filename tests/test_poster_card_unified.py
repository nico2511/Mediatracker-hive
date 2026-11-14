"""PosterCard component is shared across home, catalog and library grids."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / "frontend" / "src" / "styles.css"
HOME_BLOCKS = ROOT / "frontend" / "src" / "components" / "HomePersonalBlocks.vue"


def test_key_views_import_poster_card():
    views = (
        ROOT / "frontend" / "src" / "views" / "HomeView.vue",
        ROOT / "frontend" / "src" / "views" / "CatalogView.vue",
        ROOT / "frontend" / "src" / "views" / "MediaListView.vue",
        ROOT / "frontend" / "src" / "views" / "LibraryView.vue",
    )
    for path in views:
        source = path.read_text(encoding="utf-8")
        assert "PosterCard" in source
        assert "pc-grid" in source


def test_home_to_watch_episode_badge_uses_overlay_slot():
    blocks = HOME_BLOCKS.read_text(encoding="utf-8")
    card = (ROOT / "frontend" / "src" / "components" / "PosterCard.vue").read_text(encoding="utf-8")
    badge_block = blocks.split('title="À voir"', 1)[1].split("HomeGridSection", 1)[0]
    assert "#overlay" in badge_block
    assert "poster-badge" in badge_block
    assert 'name="overlay"' in card


def test_poster_card_touch_hover_rule_present():
    css = CSS.read_text(encoding="utf-8")
    assert "@media (hover: none), (max-width: 720px)" in css
    assert ".pc-fiche" in css
