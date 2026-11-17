"""Journal page route and navigation entry."""

from pathlib import Path

from mediatracker.retention import EVENT_JOURNAL_DAYS

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


def test_journal_period_filter_matches_event_retention():
    source = VIEW.read_text(encoding="utf-8")
    assert "Tout l'historique" not in source
    assert "jours conservés" in source
    assert "/api/v1/settings/fixed-retention" in source
    assert "event_journal_days" in source
    assert f"Tout ({EVENT_JOURNAL_DAYS} jours conservés)" not in source
