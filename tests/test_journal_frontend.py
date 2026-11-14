"""Journal page route and navigation entry."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUTER = ROOT / "frontend" / "src" / "router.ts"
LAYOUT = ROOT / "frontend" / "src" / "layouts" / "AppLayout.vue"
VIEW = ROOT / "frontend" / "src" / "views" / "JournalView.vue"


def test_journal_route_and_menu_entry_exist():
    router = ROUTER.read_text(encoding="utf-8")
    layout = LAYOUT.read_text(encoding="utf-8")
    assert VIEW.is_file()
    assert 'path: "journal"' in router
    assert "JournalView" in router
    assert 'to: "/journal"' in layout or 'to="/journal"' in layout
    assert "Journal" in layout
    assert "sidebar-link" in layout
