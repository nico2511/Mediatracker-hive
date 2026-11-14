"""Historique d'état des sources : transition unique, pas d'alerte répétée, retour."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from tests.conftest import login

from mediatracker.core.source_state import (
    STATE_DOWN,
    STATE_OK,
    observe_hoster_outcome,
    observe_source_outcome,
    recent_changes,
)
from mediatracker.db.models import Download, Event, Link, Media, MediaItem, Provider, SourceState, SourceStateChange


def _moment(offset_minutes: int = 0) -> datetime:
    return datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc) + timedelta(minutes=offset_minutes)


def test_source_down_transition_records_once(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        t0 = _moment()
        observe_source_outcome(db, type_name="wawacity", up=False, message="Domaine injoignable.", when=t0)
        db.commit()
        observe_source_outcome(db, type_name="wawacity", up=False, message="Toujours injoignable.", when=_moment(5))
        db.commit()

        changes = db.scalar(select(func.count()).select_from(SourceStateChange))
        assert changes == 1
        row = db.get(SourceState, "source:wawacity")
        assert row is not None and row.state == STATE_DOWN

        outages = db.scalar(select(func.count()).select_from(Event).where(Event.type == "source.outage")) or 0
        assert outages == 1
    finally:
        db.close()


def test_source_recovery_after_outage(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        observe_source_outcome(db, type_name="comet", up=False, message="Comet injoignable.", when=_moment())
        db.commit()
        observe_source_outcome(db, type_name="comet", up=True, message="Comet répond.", when=_moment(30))
        db.commit()

        changes = list(recent_changes(db, limit=10))
        assert len(changes) == 2
        assert changes[0].state == STATE_OK
        assert changes[1].state == STATE_DOWN

        recovered = db.scalar(select(func.count()).select_from(Event).where(Event.type == "source.recovered")) or 0
        assert recovered == 1
    finally:
        db.close()


def test_hoster_outage_and_recovery(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        observe_hoster_outcome(db, hoster="rapidgator", up=False, message="Hébergeur indisponible.", when=_moment())
        db.commit()
        observe_hoster_outcome(db, hoster="rapidgator", up=False, when=_moment(2))
        db.commit()
        observe_hoster_outcome(db, hoster="rapidgator", up=True, when=_moment(10))
        db.commit()

        assert db.scalar(select(func.count()).select_from(SourceStateChange)) == 2
        outages = db.scalar(select(func.count()).select_from(Event).where(Event.type == "source.outage")) or 0
        recovered = db.scalar(select(func.count()).select_from(Event).where(Event.type == "source.recovered")) or 0
        assert outages == 1
        assert recovered == 1
    finally:
        db.close()


def test_health_sources_api(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        observe_source_outcome(db, type_name="wawacity", up=True, when=_moment())
        db.commit()
    finally:
        db.close()
    body = client.get("/api/v1/health/sources")
    assert body.status_code == 200, body.text
    payload = body.json()
    assert any(row["key"] == "source:wawacity" for row in payload["targets"])
    assert payload["changes"]


def test_health_sources_exposes_last_debrid_at(client, app):
    login(client)
    db = app.state.session_factory()
    debrid_moment = _moment(45)
    try:
        media = Media(type="series", title="Série Démo", year=2099)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, season=1, episode=1, state="missing")
        db.add(item)
        db.flush()
        provider = db.query(Provider).filter(Provider.type == "wawacity").one()
        link = Link(
            media_item_id=item.id,
            provider_id=provider.id,
            kind="ddl_hoster",
            url="https://rapidgator.net/file/demo",
            hoster="rapidgator",
            external_id="demo-rg",
        )
        db.add(link)
        db.flush()
        db.add(
            Download(
                media_item_id=item.id,
                link_id=link.id,
                state="queued",
                debrid_link="https://redirect.alldebrid.com/demo",
                attempt_started_at=debrid_moment,
            )
        )
        observe_hoster_outcome(db, hoster="rapidgator", up=True, when=_moment())
        db.commit()
    finally:
        db.close()

    body = client.get("/api/v1/health/sources")
    assert body.status_code == 200, body.text
    hoster = next(row for row in body.json()["targets"] if row["key"] == "hoster:rapidgator")
    assert hoster["last_debrid_at"] is not None
    assert hoster["last_debrid_at"].startswith("2026-10-18")


def test_health_sources_last_debrid_never_without_download(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        observe_hoster_outcome(db, hoster="turbobit", up=True, when=_moment())
        db.commit()
    finally:
        db.close()

    body = client.get("/api/v1/health/sources")
    assert body.status_code == 200, body.text
    hoster = next(row for row in body.json()["targets"] if row["key"] == "hoster:turbobit")
    assert hoster.get("last_debrid_at") is None
