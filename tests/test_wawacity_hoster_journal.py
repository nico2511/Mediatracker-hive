"""Journal issue refused when stored hoster links exist but fail the profile."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from mediatracker.core.pipeline.unlock import _from_hosters
from mediatracker.core.quality_profile import default_profile

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)


def test_from_hosters_journals_refused_when_links_present_but_profile_rejects():
    link = SimpleNamespace(
        id=1,
        media_item_id=9,
        dead=False,
        kind="ddl_protected",
        hoster="rapidgator",
        quality="HD",
        language="VFF",
        priority=0,
        size=None,
        filename="demo.mkv",
    )
    row = SimpleNamespace(
        id=42,
        media_item_id=9,
        skipped_link_ids=[],
        state="pending",
        error_code=None,
        error=None,
    )
    item = SimpleNamespace(id=9, season=None, episode=None)
    media = SimpleNamespace(id=3, type="film", title="Odyssée Labo", year=2026)
    db = MagicMock()
    db.scalars.return_value.all.return_value = [link]

    journal_calls: list[str] = []

    def _capture_journal(db, r, m, it, source, *, issue, reason=None, now=None, link_count=None):
        del db, r, m, it, source, now, link_count
        journal_calls.append(issue if reason is None else f"{issue}:{reason}")

    with (
        patch("mediatracker.core.pipeline.unlock._journal_source_attempt", side_effect=_capture_journal),
        patch("mediatracker.core.pipeline.unlock.record_download_pass_verdict"),
        patch("mediatracker.core.profile_fallback.media_has_profile_repli_chain", return_value=False),
        patch("mediatracker.core.download_bitrate_sanity.filter_stored_links", return_value=[link]),
        patch("mediatracker.core.profile_attempt_adapters.choose_link_for_profile_attempt", return_value=None),
        patch(
            "mediatracker.core.profile_attempt_orchestration.resolve_download_poll_profile",
            return_value=SimpleNamespace(missing_explicit=False, profile=default_profile()),
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

    assert any(call.startswith("refused:filtre profil") for call in journal_calls)


def test_from_hosters_keeps_debrid_error_after_all_hosters_fail():
    from mediatracker.providers.base import DebridError

    link = SimpleNamespace(
        id=1,
        media_item_id=9,
        dead=False,
        kind="ddl_protected",
        hoster="rapidgator",
        quality="1080p",
        language="MULTI",
        priority=0,
        size=None,
        filename="demo.mkv",
        url="https://dl-protect.link/x",
    )
    row = SimpleNamespace(
        id=42,
        media_item_id=9,
        skipped_link_ids=[],
        state="pending",
        error_code=None,
        error=None,
        link_id=None,
        attempts=0,
        attempt_started_at=None,
    )
    item = SimpleNamespace(id=9, season=None, episode=None)
    media = SimpleNamespace(id=3, type="film", title="Odyssée Labo", year=2026)
    db = MagicMock()
    db.scalars.return_value.all.return_value = [link]

    verdicts: list[tuple[str, str, str]] = []

    def _capture_verdict(r, source, *, code, message, issue):
        del r, source
        verdicts.append((code, message, issue))

    profile = default_profile()

    choose_calls = {"n": 0}

    def _choose(*args, **kwargs):
        del args, kwargs
        choose_calls["n"] += 1
        return link if choose_calls["n"] == 1 else None

    with (
        patch("mediatracker.core.pipeline.unlock._journal_source_attempt"),
        patch("mediatracker.core.pipeline.unlock.record_download_pass_verdict", side_effect=_capture_verdict),
        patch("mediatracker.core.profile_fallback.media_has_profile_repli_chain", return_value=False),
        patch("mediatracker.core.download_bitrate_sanity.filter_stored_links", return_value=[link]),
        patch("mediatracker.core.profile_attempt_adapters.choose_link_for_profile_attempt", side_effect=_choose),
        patch(
            "mediatracker.core.profile_attempt_orchestration.resolve_download_poll_profile",
            return_value=SimpleNamespace(missing_explicit=False, profile=profile),
        ),
        patch("mediatracker.core.pipeline.unlock._hoster_links_for_source", return_value=None),
        patch("mediatracker.core.pipeline.unlock._hoster_supported", return_value=True),
        patch("mediatracker.core.pipeline.unlock._commit"),
        patch(
            "mediatracker.core.pipeline.unlock._unlock",
            side_effect=DebridError("LINK_HOST_UNAVAILABLE", "hébergeur indisponible", retryable=False),
        ),
        patch("mediatracker.core.pipeline.unlock._bury"),
    ):
        _from_hosters(
            db,
            row,
            item,
            media,
            NOW,
            ["rapidgator"],
            MagicMock(),
            MagicMock(),
            SimpleNamespace(),
            [],
            defer_final=True,
        )

    assert verdicts
    assert verdicts[-1][0] == "LINK_HOST_UNAVAILABLE"
    assert verdicts[-1][0] != "PROFILE"
