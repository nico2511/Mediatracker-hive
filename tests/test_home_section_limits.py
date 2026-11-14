"""Accueil : limites fixes 7/6 cartes et absence de mesure JS."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / "frontend" / "src" / "views" / "HomeView.vue"
CSS = ROOT / "frontend" / "src" / "styles.css"


def _tsx(script: str):
    import subprocess

    return subprocess.run(
        ["npx", "--yes", "tsx", "-e", script],
        cwd=ROOT / "frontend",
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_home_limits_for_item_counts():
    script = r"""
import {
  hiddenHomeItemCount,
  visibleHomeItemCount,
  HOME_DESKTOP_POSTER_CAP,
  HOME_MOBILE_POSTER_CAP,
} from "./src/homeSectionLimits.ts";

const desktopCases = [
  [0, 0, 0],
  [1, 1, 0],
  [3, 3, 0],
  [6, 6, 0],
  [7, 7, 0],
  [12, 7, 5],
];
for (const [total, visible, hidden] of desktopCases) {
  const v = visibleHomeItemCount(total, false, true);
  const h = hiddenHomeItemCount(total, false, true);
  if (v !== visible || h !== hidden) {
    throw new Error(`desktop ${total}: v=${v} h=${h}`);
  }
}
if (HOME_DESKTOP_POSTER_CAP !== 7 || HOME_MOBILE_POSTER_CAP !== 6) {
  throw new Error("caps");
}
const mobile = visibleHomeItemCount(12, false, false);
if (mobile !== 6) throw new Error(`mobile=${mobile}`);
if (hiddenHomeItemCount(12, false, false) !== 6) throw new Error("mobile hidden");
if (visibleHomeItemCount(3, false, false) !== 3) throw new Error("mobile partial");
"""
    proc = _tsx(script)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_home_view_has_no_width_measurement_js():
    source = HOME.read_text(encoding="utf-8")
    assert "posterGrid" not in source
    assert "ResizeObserver" not in source
    assert "row-columns" not in source
    assert "bindPosterMeasureGrid" not in source
    assert "getComputedStyle" not in source
    assert 'matchMedia("(min-width: 720px)")' in source
    assert "pc-grid--home" in source
    assert "pc-grid--row" not in source


def test_home_desktop_flex_scroll_and_mobile_two_rows():
    css = CSS.read_text(encoding="utf-8")
    assert "pc-grid--home" in css
    assert "flex-wrap: nowrap" in css
    assert "overflow-x: auto" in css
    mobile = css.split("@media (max-width: 720px)", 1)[1]
    assert "pc-grid--home" in mobile
    assert "repeat(3" in mobile


def test_multiple_sections_can_each_show_small_counts():
    """Chaque section slice indépendamment : 3 + 2 éléments → 3 et 2 visibles."""
    script = r"""
import { visibleHomeItemCount } from "./src/homeSectionLimits.ts";
const a = visibleHomeItemCount(3, false, true);
const b = visibleHomeItemCount(2, false, true);
if (a !== 3 || b !== 2) throw new Error(`${a} ${b}`);
"""
    proc = _tsx(script)
    assert proc.returncode == 0, proc.stdout + proc.stderr
