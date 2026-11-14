"""Appui long et menu d'actions sur PosterCard (v2 longpress)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CARD = ROOT / "frontend" / "src" / "components" / "PosterCard.vue"
COMPOSABLE = ROOT / "frontend" / "src" / "composables" / "useLongPress.ts"
CSS = ROOT / "frontend" / "src" / "styles.css"


def test_use_long_press_composable_exists():
    source = COMPOSABLE.read_text(encoding="utf-8")
    assert "export const LONG_PRESS_MS = 450" in source
    assert "export function useLongPress" in source


def test_poster_card_wires_long_press_and_menu():
    source = CARD.read_text(encoding="utf-8")
    assert "useLongPress" in source
    assert '@contextmenu="press.onContextmenu"' in source
    assert 'role="menu"' in source
    assert 'role="menuitem"' in source
    assert 'class="pc-actions"' in source
    assert 'aria-haspopup="menu"' in source
    assert "Teleport" in source
    assert 'draggable="false"' in source


def test_styles_hide_mobile_actions_and_sheet_menu():
    css = CSS.read_text(encoding="utf-8")
    assert ".pc-actions" in css
    assert ".pc-menu" in css
    assert ".sr-only" in css
    block_start = css.index("@media (hover: none), (max-width: 720px)")
    mobile_block = css[block_start : block_start + 2500]
    assert ".pc-actions" in mobile_block
    assert "display: none" in mobile_block
    assert "touch-action: manipulation" in mobile_block
