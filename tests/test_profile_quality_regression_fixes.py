"""Journal empty/refused, invalidation sondage, reprise après échec « pas de lien »."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy import select

from mediatracker.core.pipeline.unlock import _from_hosters, _last_poll_rejected_by_profile
from mediatracker.core.quality_profile import default_profile, refresh_followed_media_for_profile_change
from mediatracker.core.source_poll import (
    _blocking_download,
    _failed_is_poll_requeue_eligible,
    _requeue_failed_download,
)
from mediatracker.db.models import Download, Media, QualityProfile

NOW = datetime(2026, 11, 15, 8, 0, tzinfo=timezone.utc)


def test_last_poll_rejected_by_profile_detects_message():
    media = SimpleNamespace(
        last_poll_result="nothing",
        last_poll_message="Aucune version ne correspond au profil « Démo ». détail",
    )
    assert _last_poll_rejected_by_profile(media)
    media.last_poll_result = "found"
    assert not _last_poll_rejected_by_profile(media)


def test_from_hosters_journals_refused_when_poll_blocked_profile():
    profile = default_profile()

    row = SimpleNamespace(
        id=42,
        media_item_id=9,
        skipped_link_ids=[],
        state="pending",
        error_code=None,
        error=None,
    )
    item = SimpleNamespace(id=9, season=4, episode=5)
    media = SimpleNamespace(
        id=3,
        type="series",
        title="Atelier Neutre",
        year=2026,
        last_poll_result="nothing",
        last_poll_message="Aucune version ne correspond au profil « VF/Multi 1080p ».",
    )
    db = MagicMock()
    db.scalars.return_value.all.return_value = []
    journal: list[tuple[str, str | None]] = []

    def _capture(db, r, m, it, source, *, issue, reason=None, now=None, link_count=None):
        del db, r, m, it, source, now, link_count
        journal.append((issue, reason))

    with (
        patch("mediatracker.core.pipeline.unlock._journal_source_attempt", side_effect=_capture),
        patch("mediatracker.core.pipeline.unlock.record_download_pass_verdict"),
        patch("mediatracker.core.profile_fallback.media_has_profile_repli_chain", return_value=False),
        patch(
            "mediatracker.core.profile_attempt_orchestration.resolve_download_poll_profile",
            return_value=SimpleNamespace(missing_explicit=False, profile=profile),
        ),
        patch("mediatracker.core.pipeline.unlock._hoster_links_for_source", return_value=None),
    ):
        _from_hosters(
            db,
            row,
            item,
            media,
            NOW,
            ["rapidgator"],
            None,
            MagicMock(),
            SimpleNamespace(),
            [],
            defer_final=True,
        )
    assert journal == [("refused", "filtre profil — VF/Multi 1080p")]


def test_profile_update_refreshes_followed_media_schedule(client, app):
    del client
    db = app.state.session_factory()
    try:
        profile = db.scalar(select(QualityProfile).where(QualityProfile.is_default.is_(True)))
        assert profile is not None
        media = Media(
            type="series",
            title="Suivi Démo",
            year=2026,
            followed=True,
            quality_profile_id=None,
            last_remote_search_at=NOW,
            next_source_poll_at=NOW + timedelta(hours=12),
        )
        db.add(media)
        db.commit()
        refresh_followed_media_for_profile_change(
            db, profile.id, affects_default=True, now=NOW + timedelta(hours=1)
        )
        db.commit()
        db.refresh(media)
        assert media.last_remote_search_at is None
        expected = NOW + timedelta(hours=1)
        actual = media.next_source_poll_at
        if actual is not None and actual.tzinfo is None:
            actual = actual.replace(tzinfo=timezone.utc)
        assert actual == expected
    finally:
        db.close()


def test_blocking_download_ignores_failed_but_blocks_active(client, app):
    del client
    db = app.state.session_factory()
    try:
        media = Media(type="series", title="File Démo", year=2026)
        db.add(media)
        db.flush()
        from mediatracker.db.models import MediaItem

        item = MediaItem(media_id=media.id, season=1, episode=1, state="available")
        db.add(item)
        db.flush()
        failed = Download(
            media_item_id=item.id,
            state="failed",
            error_code="NO_LINK",
            created_at=NOW,
        )
        db.add(failed)
        db.commit()
        assert _blocking_download(db, item.id) is False
        active = Download(media_item_id=item.id, state="downloading", created_at=NOW)
        db.add(active)
        db.commit()
        assert _blocking_download(db, item.id) is True
    finally:
        db.close()


def test_failed_debrid_error_blocks_poll_requeue(client, app):
    del client
    db = app.state.session_factory()
    try:
        from mediatracker.db.models import MediaItem

        media = Media(type="series", title="Blocage Démo", year=2026)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, season=1, episode=2, state="available")
        db.add(item)
        db.flush()
        failed = Download(
            media_item_id=item.id,
            state="failed",
            error_code="RATE_LIMIT",
            error="Trop de requêtes.",
            created_at=NOW,
        )
        db.add(failed)
        db.commit()
        assert _failed_is_poll_requeue_eligible(failed) is False
        assert _blocking_download(db, item.id) is True
        assert _requeue_failed_download(db, item.id, NOW, poll_quality_profile_id=None) is None
    finally:
        db.close()


def test_requeue_failed_download_resets_row(client, app):
    del client
    db = app.state.session_factory()
    try:
        from mediatracker.db.models import MediaItem

        media = Media(type="series", title="Requeue Démo", year=2026)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, season=1, episode=3, state="available")
        db.add(item)
        db.flush()
        failed = Download(
            media_item_id=item.id,
            state="failed",
            error_code="NO_LINK",
            error="test",
            attempts=3,
            created_at=NOW - timedelta(hours=1),
        )
        db.add(failed)
        db.commit()
        moment = NOW + timedelta(minutes=5)
        row = _requeue_failed_download(db, item.id, moment, poll_quality_profile_id=7)
        assert row is not None
        assert row.state == "pending"
        assert row.error_code is None
        assert row.attempts == 0
        assert row.poll_quality_profile_id == 7
    finally:
        db.close()