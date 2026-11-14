"""Action menu closes on click so menuitem handlers still run."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "frontend" / "src" / "components" / "ActionMenuPopover.vue"


def test_menu_closes_on_click_not_pointerdown():
    source = SOURCE.read_text(encoding="utf-8")
    assert '@click="onMenuClick"' in source
    assert "onMenuPointerDown" not in source
    assert "function onMenuClick" in source
