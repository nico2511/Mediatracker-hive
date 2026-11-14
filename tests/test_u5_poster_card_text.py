"""UI-d: poster card titles and hints stay inside their grid column."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / "frontend" / "src" / "styles.css"


def test_poster_grid_children_fill_column_width():
    css = CSS.read_text(encoding="utf-8")
    block = css.split(".poster-grid > .poster-card,", 1)[1].split("}", 1)[0]
    assert "width: 100%" in block
    assert "min-width: 0" in block


def test_poster_card_text_uses_clamp_and_ellipsis():
    css = CSS.read_text(encoding="utf-8")
    strong = css.split(".poster-card strong {", 1)[1].split("}", 1)[0]
    assert "white-space: nowrap" in strong
    assert "text-overflow: ellipsis" in strong
    assert "min-width: 0" in strong

    hint = css.split(".poster-card .hint {", 1)[1].split("}", 1)[0]
    assert "-webkit-line-clamp: 2" in hint
    assert "min-width: 0" in hint
    assert "overflow: hidden" in hint

    base = css.split(".poster-card {", 1)[1].split("}", 1)[0]
    assert "min-width: 0" in base


def test_poster_row_cards_are_width_bounded():
    css = CSS.read_text(encoding="utf-8")
    row = css.split(".poster-row > .poster-card {", 1)[1].split("}", 1)[0]
    assert "min-width: 0" in row
    assert "max-width: 8.25rem" in row
