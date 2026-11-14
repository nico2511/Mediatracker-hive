"""Réglages Sources : statut et dernier test par source."""

from datetime import datetime, timezone

from tests.conftest import csrf, login

from mediatracker.core.download_sources_status import download_source_statuses
from mediatracker.core.source_probe import parse_probe_query, probe_download_sources
from mediatracker.core.source_state import (
    PROBE_EMPTY_MESSAGE,
    STATE_DOWN,
    STATE_OK,
    note_probe_source_health,
    observe_source_outcome,
    record_source_probe,
)
from mediatracker.db.models import SourceState


def test_record_source_probe_persists_last_success(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        record_source_probe(
            db,
            type_name="wawacity",
            issue="found",
            link_count=4,
            duration_ms=820,
            when=datetime(2026, 11, 9, 20, 38, tzinfo=timezone.utc),
        )
        db.commit()
        bundle = download_source_statuses(db, app.state.settings)
        wawa = next(item for item in bundle.sources if item.source == "wawacity")
        assert wawa.last_probe_link_count == 4
        assert wawa.last_success_at is not None
    finally:
        db.close()


def test_sources_status_endpoint(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        record_source_probe(
            db,
            type_name="comet",
            issue="found",
            link_count=6,
            duration_ms=1200,
            when=datetime(2026, 11, 9, 21, 0, tzinfo=timezone.utc),
        )
        db.commit()
    finally:
        db.close()
    response = client.get("/api/v1/settings/sources/status", headers=csrf(client))
    assert response.status_code == 200, response.text
    names = [row["source"] for row in response.json()["sources"]]
    assert "wawacity" in names
    comet = next(row for row in response.json()["sources"] if row["source"] == "comet")
    assert comet["last_probe_link_count"] == 6


def test_public_source_message_masks_before_truncate():
    from mediatracker.core.source_state import public_source_message

    filler = " ".join(["mot"] * 130)
    assert len(filler) > 500
    secret = "fictivealldebridkeyabcdefghijklmnopqrstuvwx"
    raw = f"{filler} suffixe {secret}"
    assert len(raw) > 500 + len(secret)
    cleaned = public_source_message(raw)
    assert secret not in cleaned
    assert len(cleaned) <= 500
    assert cleaned.startswith("mot mot")


def test_public_source_message_redacts_bare_alldebrid_key():
    from mediatracker.core.source_state import public_source_message

    bare = "fictivealldebridkeyabcdefghijklmnopqrstuvwx"
    cleaned = public_source_message(f"HTTPStatusError : clé refusée ({bare})")
    assert bare not in cleaned
    assert "[redacted]" in cleaned
    assert public_source_message("Service temporairement indisponible.") == "Service temporairement indisponible."


def test_public_source_message_keeps_hex_digests_and_uuid():
    from mediatracker.core.source_state import public_source_message

    md5 = "a" * 32
    sha1 = "b" * 40
    sha256 = "c" * 64
    digest_msg = f"HTTPStatusError : etag={md5} digest={sha1} checksum={sha256}"
    assert public_source_message(digest_msg) == digest_msg
    ref_uuid = "00000000-0000-4000-8000-000000000001"
    uuid_msg = f"HTTPStatusError : référence {ref_uuid}"
    assert public_source_message(uuid_msg) == uuid_msg


def test_probe_error_reason_masks_secrets(client, app, monkeypatch):
    from mediatracker.core.source_state import public_source_message

    login(client)
    marker = "fictive-probe-secret-token-xyz"
    payload = f"HTTPStatusError : GET https://api.example/v1?apikey={marker}&q=1 failed"
    cleaned = public_source_message(payload)
    assert marker not in cleaned
    assert "[redacted]" in cleaned

    def boom(*_args, **_kwargs):
        raise RuntimeError(payload)

    monkeypatch.setattr("mediatracker.core.source_probe._probe_one", boom)
    monkeypatch.setattr("mediatracker.core.source_probe._source_disabled", lambda *a, **k: None)
    monkeypatch.setattr("mediatracker.core.source_probe.legacy_comet_context", lambda *a, **k: (None, None))
    monkeypatch.setattr("mediatracker.core.source_probe._default_profile", lambda _db: None)
    db = app.state.session_factory()
    try:
        target = parse_probe_query("Film Démo")
        rows = probe_download_sources(db, app.state.settings, target, sources=["wawacity"])
        db.commit()
        assert rows[0].issue == "error"
        assert rows[0].reason == public_source_message(f"RuntimeError : {payload}")
        assert marker not in (rows[0].reason or "")
        note_probe_source_health(
            db,
            type_name="comet",
            issue="error",
            reason=f"token={marker}",
            when=datetime(2026, 11, 9, 22, 0, tzinfo=timezone.utc),
        )
        db.commit()
        row = db.get(SourceState, "source:comet")
        assert row is not None
        assert marker not in (row.message or "")
    finally:
        db.close()


def test_note_probe_source_health_empty_clears_outage(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        moment = datetime(2026, 11, 10, 8, 0, tzinfo=timezone.utc)
        observe_source_outcome(
            db,
            type_name="wawacity",
            up=False,
            message="AttributeError : pref_quality manquant",
            when=moment,
        )
        db.flush()
        row = db.get(SourceState, "source:wawacity")
        assert row is not None
        assert row.state == STATE_DOWN

        later = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)
        record_source_probe(
            db,
            type_name="wawacity",
            issue="empty",
            link_count=0,
            duration_ms=120,
            reason=None,
            when=later,
        )
        note_probe_source_health(db, type_name="wawacity", issue="empty", reason=None, when=later)
        db.commit()

        row = db.get(SourceState, "source:wawacity")
        assert row is not None
        assert row.state == STATE_OK
        assert row.message == PROBE_EMPTY_MESSAGE
        assert row.last_probe_issue == "empty"

        bundle = download_source_statuses(db, app.state.settings)
        wawa = next(item for item in bundle.sources if item.source == "wawacity")
        assert wawa.state == STATE_OK
        assert wawa.last_probe_issue == "empty"
    finally:
        db.close()


def test_note_probe_source_health_error_replaces_stale_outage_message(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        moment = datetime(2026, 11, 10, 7, 0, tzinfo=timezone.utc)
        observe_source_outcome(
            db,
            type_name="wawacity",
            up=False,
            message="AttributeError : objet str sans attribut url",
            when=moment,
        )
        db.flush()
        row = db.get(SourceState, "source:wawacity")
        assert row is not None
        assert row.state == STATE_DOWN
        assert "url" in (row.message or "")

        later = datetime(2026, 11, 10, 7, 30, tzinfo=timezone.utc)
        record_source_probe(
            db,
            type_name="wawacity",
            issue="error",
            link_count=0,
            duration_ms=50,
            reason="SourceFetchError : page catalogue indisponible",
            when=later,
        )
        note_probe_source_health(
            db,
            type_name="wawacity",
            issue="error",
            reason="SourceFetchError : page catalogue indisponible",
            when=later,
        )
        db.commit()

        row = db.get(SourceState, "source:wawacity")
        assert row is not None
        assert row.state == STATE_DOWN
        assert "catalogue indisponible" in (row.message or "")
        assert "objet str sans attribut url" not in (row.message or "")
        assert row.last_probe_reason == "SourceFetchError : page catalogue indisponible"
    finally:
        db.close()


def test_note_probe_source_health_error_sets_outage(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        moment = datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc)
        record_source_probe(
            db,
            type_name="comet",
            issue="error",
            link_count=0,
            duration_ms=90,
            reason="HTTPStatusError : page indisponible",
            when=moment,
        )
        note_probe_source_health(
            db,
            type_name="comet",
            issue="error",
            reason="HTTPStatusError : page indisponible",
            when=moment,
        )
        db.commit()
        row = db.get(SourceState, "source:comet")
        assert row is not None
        assert row.state == STATE_DOWN
        assert "page indisponible" in (row.message or "")
        assert row.last_probe_reason == "HTTPStatusError : page indisponible"
    finally:
        db.close()


def test_probe_sources_accepts_source_filter(client, monkeypatch):
    login(client)
    seen: list[str | None] = []

    def fake_probe(_db, _settings, target, *, now=None, sources=None):
        seen.append(sources)
        return []

    monkeypatch.setattr("mediatracker.api.v1.settings.probe_download_sources", fake_probe)
    response = client.post(
        "/api/v1/settings/sources/probe",
        json={"query": "Film Démo", "sources": ["wawacity"]},
        headers=csrf(client),
    )
    assert response.status_code == 200, response.text
    assert seen == [["wawacity"]]
