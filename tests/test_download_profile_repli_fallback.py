"""Repli profil pendant la passe de téléchargement (hive, sans titres réels)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from tests.conftest import login

from mediatracker.core.pipeline.jobs import (
    _ingest_other_versions,
    advance_download_profile_repli,
    peek_next_download_profile_repli,
)
from mediatracker.core.profile_attempt_orchestration import (
    next_profile_repli_in_chain,
    orchestrate_profile_first_poll,
    profile_chain_from_anchor,
)
from mediatracker.core.profile_fallback import (
    encode_media_fallback_profile_ids,
)
from mediatracker.core.quality_profile import profile_from_row
from mediatracker.core.source_poll import PollReport
from mediatracker.db.models import Download, Event, Media, MediaItem, QualityProfile
from mediatracker.db.session import commit_retry

NOW = datetime(2026, 11, 15, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(client):
    session = client.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


def _add_profile(db, name: str, *, languages: tuple[str, ...], default: bool = False) -> QualityProfile:
    row = QualityProfile(
        name=name,
        languages=list(languages),
        min_resolution=None,
        max_resolution="1080p",
        is_default=default,
    )
    db.add(row)
    db.flush()
    return row


def _media_with_chain(db, principal: QualityProfile, replis: list[QualityProfile]) -> Media:
    media = Media(
        type="film",
        title="Chaîne repli test",
        year=2020,
        followed=True,
        quality_profile_id=principal.id,
        fallback_profile_ids=encode_media_fallback_profile_ids([r.id for r in replis]),
        source_order="mock_a",
    )
    db.add(media)
    db.flush()
    return media


def test_profile_chain_from_anchor_slices_tail(client, db, settings):
    principal = _add_profile(db, "Profil P", languages=("VFF",))
    r1 = _add_profile(db, "Profil R1", languages=("VOSTFR",))
    r2 = _add_profile(db, "Profil R2", languages=("VF",))
    media = _media_with_chain(db, principal, [r1, r2])
    commit_retry(db)
    anchor = profile_from_row(r1)
    chain = profile_chain_from_anchor(db, media, settings, anchor)
    assert [p.name for p in chain.profiles] == ["Profil R1", "Profil R2"]
    nxt = next_profile_repli_in_chain(db, media, settings, profile_from_row(principal))
    assert nxt is not None and nxt.rank == 2 and nxt.profile.name == "Profil R1"


def test_advance_download_profile_repli_and_journal(client, app, db, settings):
    login(client)
    principal = _add_profile(db, "Profil P", languages=("VFF",))
    r1 = _add_profile(db, "Profil R1", languages=("VOSTFR",))
    media = _media_with_chain(db, principal, [r1])
    item = MediaItem(media_id=media.id, state="missing")
    db.add(item)
    db.flush()
    dl = Download(media_item_id=item.id, state="pending", poll_quality_profile_id=None)
    db.add(dl)
    commit_retry(db)
    assert peek_next_download_profile_repli(db, media, settings, dl) is not None
    assert advance_download_profile_repli(db, media, item, dl, settings, now=NOW)
    assert dl.poll_quality_profile_id == r1.id
    ev = db.scalar(select(Event).where(Event.type == "download.profile_repli").order_by(Event.id.desc()))
    assert ev is not None
    assert "Profil R1" in str(ev.payload.get("message"))
    assert ev.payload.get("rank") == 2


def test_no_next_repli_when_chain_exhausted(client, db, settings):
    principal = _add_profile(db, "Seul P", languages=("VFF",))
    media = Media(type="film", title="Sans repli", year=2021, quality_profile_id=principal.id, fallback_profile_ids="")
    db.add(media)
    db.flush()
    item = MediaItem(media_id=media.id, state="missing")
    db.add(item)
    db.flush()
    dl = Download(media_item_id=item.id, state="pending", poll_quality_profile_id=principal.id)
    db.add(dl)
    commit_retry(db)
    assert peek_next_download_profile_repli(db, media, settings, dl) is None


def test_ingest_from_next_repli_uses_later_profile_criteria(client, app, monkeypatch):
    db = app.state.session_factory()
    try:
        principal = _add_profile(db, "Profil P", languages=("VFF",))
        r1 = _add_profile(db, "Profil R1", languages=("VOSTFR",))
        media = _media_with_chain(db, principal, [r1])
        item = MediaItem(media_id=media.id, season=1, episode=1, state="missing")
        db.add(item)
        commit_retry(db)
        media_id, item_id = media.id, item.id
    finally:
        db.close()

    langs_seen: list[tuple[str, ...]] = []

    class _Stub:
        capabilities = frozenset({"links"})

        def search(self, *_a, **_k):
            return []

        def get_links(self, *_a, **_k):
            return []

        def close(self) -> None:
            return None

    monkeypatch.setattr("mediatracker.providers.sources.registry.for_type", lambda _n: _Stub)
    monkeypatch.setattr(
        "mediatracker.core.pipeline.jobs._usable_download_sources",
        lambda *_a, **_k: ["mock_a"],
    )
    monkeypatch.setattr(
        "mediatracker.providers.sources.loading.remote_links_source",
        lambda *_a, **_k: (1, _Stub()),
    )
    monkeypatch.setattr("mediatracker.core.remote_search_limit.begin_remote_search", lambda *_a, **_k: True)

    def capture_search(_source, _media, _seasons, _quality, language, *_a, **_k):
        langs_seen.append(language)
        return []

    monkeypatch.setattr("mediatracker.core.source_poll._search", capture_search)

    db2 = app.state.session_factory()
    try:
        media = db2.get(Media, media_id)
        item = db2.get(MediaItem, item_id)
        spec_p = profile_from_row(db2.get(QualityProfile, principal.id))
        assert media is not None and item is not None and spec_p is not None
        _ingest_other_versions(db2, app.state.settings, media, item, NOW, attempt_profile=spec_p, from_next_repli=True)
        assert langs_seen == ["VOSTFR"]
    finally:
        db2.close()


def test_orchestrate_restrict_single_profile_tries_chain_tail(client, app, db, settings, monkeypatch):
    login(client)
    principal = _add_profile(db, "Profil P", languages=("VFF",))
    r1 = _add_profile(db, "Profil R1", languages=("VOSTFR",))
    r2 = _add_profile(db, "Profil R2", languages=("VF",))
    media = _media_with_chain(db, principal, [r1, r2])
    commit_retry(db)

    tried: list[str] = []

    def worker(*_a, single_profile=None, **_k):
        if single_profile is not None:
            tried.append(single_profile.name)
        return PollReport(
            result="nothing",
            message="",
            episodes=0,
            queued=0,
            proposed=0,
            last_check_at=NOW,
            next_check_at=NOW,
        )

    monkeypatch.setattr("mediatracker.core.source_poll_phased.worker_remote_poll", worker)
    monkeypatch.setattr(
        "mediatracker.core.profile_attempt_orchestration.download_source_order",
        lambda *_a, **_k: ("wawacity",),
    )
    monkeypatch.setattr(
        "mediatracker.providers.sources.loading.remote_links_source",
        lambda *_a, **_k: (1, MagicMock(close=lambda: None)),
    )
    monkeypatch.setattr("mediatracker.hive.identity.hive_enabled_in_db", lambda _db: False)
    monkeypatch.setattr("mediatracker.core.remote_search_limit.begin_remote_search", lambda *_a, **_k: True)

    anchor = profile_from_row(r1)
    orchestrate_profile_first_poll(
        app.state.session_factory,
        settings,
        media.id,
        force=True,
        now=NOW,
        restrict_single_profile=anchor,
    )
    assert tried == ["Profil R1", "Profil R2"]
