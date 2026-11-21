"""Wawacity page « VF HD » vs fichier réel (link/infos avant débridage)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import select
from tests.conftest import profile_compliant_release_filename

from mediatracker.core.pipeline.unlock import _from_hosters
from mediatracker.core.profile_attempt_adapters import rank_link_for_profile_attempt
from mediatracker.core.profile_fallback import (
    GLOBAL_ALLOW_FALLBACK_KEY,
    PROFILE_FALLBACK_ORDER_KEY,
    format_profile_fallback_order,
)
from mediatracker.core.quality_profile import default_profile, ensure_default_profile
from mediatracker.db.models import Download, Link, Media, MediaItem, Provider, QualityProfile
from mediatracker.providers.base import DirectLink, Health
from mediatracker.providers.debrid.alldebrid import LinkInspection

NOW = datetime(2026, 11, 21, 18, 0, tzinfo=timezone.utc)


def _upsert_setting(db, key: str, value: str) -> None:
    from mediatracker.db.models import Setting

    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value, is_secret=False))
    else:
        row.value = value
    db.commit()


class InspectingDebrid:
    def __init__(self, *, filename: str) -> None:
        self.filename = filename
        self.seen_unlock: list[str] = []
        self.seen_inspect: list[str] = []

    def health(self) -> Health:
        return Health(status="ok", code=None, message="ok", checked_at=NOW)

    def resolve(self, url: str) -> list[str]:
        return [url]

    def inspect_link(self, url: str) -> LinkInspection:
        self.seen_inspect.append(url)
        return LinkInspection(
            link=url,
            online=True,
            filename=self.filename,
            size=800_000_000,
            code=None,
            message=None,
        )

    def unlock(self, url: str) -> DirectLink:
        self.seen_unlock.append(url)
        return DirectLink(url="https://cdn.example/video.mkv", filename=self.filename, size=800_000_000)


@pytest.fixture
def db_session(client):
    session = client.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


def test_page_hd_label_provisionally_accepted_by_1080p_profile(db_session, settings):
    ensure_default_profile(db_session)
    profile = default_profile()
    provider = db_session.scalar(select(Provider).where(Provider.type == "wawacity"))
    link = Link(
        provider_id=provider.id,
        provider=provider,
        quality="HD",
        language="MULTI",
        hoster="rapidgator",
        kind="ddl_protected",
        url="https://dl-protect.link/page-hd",
        priority=0,
        filename=None,
        external_id="wawa:hd-page",
        media_item_id=1,
    )
    assert rank_link_for_profile_attempt(link, profile=profile) is not None


def test_from_hosters_hd_page_unlocks_after_link_infos_1080p(client, db_session, settings):
    ensure_default_profile(db_session)
    _upsert_setting(db_session, PROFILE_FALLBACK_ORDER_KEY, "")
    _upsert_setting(db_session, GLOBAL_ALLOW_FALLBACK_KEY, "false")

    media = Media(type="series", title="Parcours neutre", year=2024, followed=True)
    db_session.add(media)
    db_session.flush()
    item = MediaItem(media_id=media.id, season=1, episode=2, state="missing")
    db_session.add(item)
    db_session.flush()
    provider = db_session.scalar(select(Provider).where(Provider.type == "wawacity"))
    release_name = profile_compliant_release_filename(season=1, episode=2)
    link = Link(
        media_item_id=item.id,
        provider_id=provider.id,
        quality="HD",
        language="MULTI",
        hoster="rapidgator",
        kind="ddl_hoster",
        url="https://rapidgator.example/demo",
        priority=0,
        filename=None,
        external_id="wawa:precheck",
    )
    db_session.add(link)
    row = Download(media_item_id=item.id, state="pending")
    db_session.add(row)
    db_session.commit()

    debrid = InspectingDebrid(filename=release_name)
    with (
        patch("mediatracker.core.pipeline.unlock._probed_direct", return_value=None),
        patch("mediatracker.core.pipeline.unlock._commit"),
        patch("mediatracker.core.pipeline.unlock._hoster_supported", return_value=True),
    ):
        outcome = _from_hosters(
            db_session,
            row,
            item,
            media,
            NOW,
            ["rapidgator"],
            None,
            debrid,
            settings,
            [],
            defer_final=True,
            source_type="wawacity",
        )

    assert outcome is not None
    assert debrid.seen_inspect
    assert debrid.seen_unlock
    assert link.quality == "1080p"
    assert link.filename == release_name
    qp = db_session.scalar(select(QualityProfile).where(QualityProfile.is_default.is_(True)))
    assert row.poll_quality_profile_id == qp.id


def test_from_hosters_rejects_true_720p_without_unlock_under_1080p_only(db_session, settings):
    ensure_default_profile(db_session)
    _upsert_setting(db_session, PROFILE_FALLBACK_ORDER_KEY, "")
    _upsert_setting(db_session, GLOBAL_ALLOW_FALLBACK_KEY, "false")

    media = Media(type="series", title="Parcours neutre", year=2024, followed=True)
    db_session.add(media)
    db_session.flush()
    item = MediaItem(media_id=media.id, season=1, episode=2, state="missing")
    db_session.add(item)
    db_session.flush()
    provider = db_session.scalar(select(Provider).where(Provider.type == "wawacity"))
    low_name = "Exemple.Fictif.2099.VFF.720p.WEB.S01E02.mkv"
    link = Link(
        media_item_id=item.id,
        provider_id=provider.id,
        quality="HD",
        language="MULTI",
        hoster="rapidgator",
        kind="ddl_hoster",
        url="https://rapidgator.example/low",
        priority=0,
        filename=None,
        external_id="wawa:720",
    )
    db_session.add(link)
    row = Download(media_item_id=item.id, state="pending")
    db_session.add(row)
    db_session.commit()

    debrid = InspectingDebrid(filename=low_name)
    with (
        patch("mediatracker.core.pipeline.unlock._probed_direct", return_value=None),
        patch("mediatracker.core.pipeline.unlock._commit"),
        patch("mediatracker.core.pipeline.unlock._hoster_supported", return_value=True),
        patch("mediatracker.core.profile_fallback.media_has_profile_repli_chain", return_value=False),
    ):
        outcome = _from_hosters(
            db_session,
            row,
            item,
            media,
            NOW,
            ["rapidgator"],
            None,
            debrid,
            settings,
            [],
            defer_final=True,
            source_type="wawacity",
        )

    assert outcome is None
    assert debrid.seen_inspect
    assert not debrid.seen_unlock
    assert link.quality == "HD"


def test_from_hosters_with_repli_still_picks_hd_page_before_unlock(db_session, settings):
    ensure_default_profile(db_session)
    repli = db_session.scalar(select(QualityProfile).where(QualityProfile.name == "720p VF"))
    assert repli is not None
    _upsert_setting(db_session, PROFILE_FALLBACK_ORDER_KEY, format_profile_fallback_order([repli.id]))
    _upsert_setting(db_session, GLOBAL_ALLOW_FALLBACK_KEY, "true")

    media = Media(type="series", title="Parcours repli", year=2024, followed=True)
    db_session.add(media)
    db_session.flush()
    item = MediaItem(media_id=media.id, season=1, episode=1, state="missing")
    db_session.add(item)
    db_session.flush()
    provider = db_session.scalar(select(Provider).where(Provider.type == "wawacity"))
    link = Link(
        media_item_id=item.id,
        provider_id=provider.id,
        quality="HD",
        language="MULTI",
        hoster="rapidgator",
        kind="ddl_hoster",
        url="https://rapidgator.example/hd",
        priority=0,
        filename=None,
        external_id="wawa:repli",
    )
    db_session.add(link)
    row = Download(media_item_id=item.id, state="pending")
    db_session.add(row)
    db_session.commit()

    release_name = profile_compliant_release_filename(season=1, episode=1)
    debrid = InspectingDebrid(filename=release_name)
    with (
        patch("mediatracker.core.pipeline.unlock._probed_direct", return_value=None),
        patch("mediatracker.core.pipeline.unlock._commit"),
        patch("mediatracker.core.pipeline.unlock._hoster_supported", return_value=True),
    ):
        outcome = _from_hosters(
            db_session,
            row,
            item,
            media,
            NOW,
            ["rapidgator"],
            None,
            debrid,
            settings,
            [],
            defer_final=True,
            source_type="wawacity",
        )

    assert outcome is not None
    principal = db_session.scalar(select(QualityProfile).where(QualityProfile.is_default.is_(True)))
    assert row.poll_quality_profile_id == principal.id


def test_from_hosters_skips_720p_on_1080p_tour_then_unlocks_other_link(db_session, settings):
    """Avant : un 720p débridé sous repli ; après : saut PROFILE au tour 1080p, 1080p pris ensuite."""
    ensure_default_profile(db_session)
    _upsert_setting(db_session, PROFILE_FALLBACK_ORDER_KEY, "")
    _upsert_setting(db_session, GLOBAL_ALLOW_FALLBACK_KEY, "false")

    media = Media(type="series", title="Deux sources", year=2024, followed=True)
    db_session.add(media)
    db_session.flush()
    item = MediaItem(media_id=media.id, season=1, episode=3, state="missing")
    db_session.add(item)
    db_session.flush()
    provider = db_session.scalar(select(Provider).where(Provider.type == "wawacity"))
    low = Link(
        media_item_id=item.id,
        provider_id=provider.id,
        quality="HD",
        language="MULTI",
        hoster="rapidgator",
        kind="ddl_hoster",
        url="https://rapidgator.example/low",
        priority=100,
        filename=None,
        external_id="wawa:low",
    )
    good_name = profile_compliant_release_filename(season=1, episode=3)
    high = Link(
        media_item_id=item.id,
        provider_id=provider.id,
        quality="HD",
        language="MULTI",
        hoster="rapidgator",
        kind="ddl_hoster",
        url="https://rapidgator.example/high",
        priority=50,
        filename=None,
        external_id="wawa:high",
    )
    db_session.add_all([low, high])
    row = Download(media_item_id=item.id, state="pending")
    db_session.add(row)
    db_session.commit()

    class _RoutingDebrid(InspectingDebrid):
        def __init__(self) -> None:
            super().__init__(filename="")
            self.low_name = "Exemple.Fictif.2099.MULTi.720p.WEB.S01E03.mkv"
            self.high_name = good_name

        def inspect_link(self, url: str) -> LinkInspection:
            name = self.low_name if "low" in url else self.high_name
            self.seen_inspect.append(url)
            return LinkInspection(
                link=url,
                online=True,
                filename=name,
                size=800_000_000,
                code=None,
                message=None,
            )

    debrid = _RoutingDebrid()
    with (
        patch("mediatracker.core.pipeline.unlock._probed_direct", return_value=None),
        patch("mediatracker.core.pipeline.unlock._commit"),
        patch("mediatracker.core.pipeline.unlock._hoster_supported", return_value=True),
        patch("mediatracker.core.profile_fallback.media_has_profile_repli_chain", return_value=False),
    ):
        outcome = _from_hosters(
            db_session,
            row,
            item,
            media,
            NOW,
            ["rapidgator"],
            None,
            debrid,
            settings,
            [],
            defer_final=True,
            source_type="wawacity",
        )

    assert outcome is not None
    assert outcome[0].id == high.id
    assert len(debrid.seen_unlock) == 1
    assert debrid.seen_inspect[0].endswith("/low")
    assert not any(url.endswith("/low") for url in debrid.seen_unlock)
    assert low.id in (row.skipped_link_ids or [])
