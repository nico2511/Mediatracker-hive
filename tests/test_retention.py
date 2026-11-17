"""Durées de conservation fixes : module et route API."""

from tests.conftest import csrf, login

from mediatracker.retention import (
    EVENT_JOURNAL_DAYS,
    INCOMPLETE_ORPHAN_KEEP_DAYS,
    METRICS_HISTORY_DAYS,
    SOURCE_STATE_DAYS,
    fixed_retention,
)


def test_fixed_retention_module_values():
    row = fixed_retention()
    assert row.event_journal_days == EVENT_JOURNAL_DAYS == 30
    assert row.source_state_days == SOURCE_STATE_DAYS == 30
    assert row.metrics_history_days == METRICS_HISTORY_DAYS == 7
    assert row.incomplete_orphan_keep_days == INCOMPLETE_ORPHAN_KEEP_DAYS == 3


def test_fixed_retention_api(client):
    login(client)
    response = client.get("/api/v1/settings/fixed-retention", headers=csrf(client))
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "event_journal_days": 30,
        "source_state_days": 30,
        "metrics_history_days": 7,
        "incomplete_orphan_keep_days": 3,
    }