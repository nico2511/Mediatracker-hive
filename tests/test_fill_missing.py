"""Missing episodes of a followed auto series are queued, once, outside the air window."""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select

from mediatracker.core.polling import EpisodeAir, PollSubject, should_poll_source
from mediatracker.core.selection import parse_hoster_preference
from mediatracker.core.source_poll import Slot, _apply, _gate, expected_slots, run_poll
from mediatracker.db.models import Download, LibraryFile, Link, Media, MediaItem, Provider
from mediatracker.providers.base import LinkCandidate, SourceRef, SourceResult

NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
TODAY = NOW.date()
WINDOW = dict(now=NOW, interval=timedelta(hours=12), ongoing_only=True, window_days=3)


def _subject(**overrides) -> PollSubject:
    base = dict(
        followed=True,
        on_hold=False,
        media_type="series",
        series_status="returning",
        new_episode_policy="auto",
        last_source_poll_at=None,
        next_source_poll_at=None,
        next_air_date=date(2026, 1, 1),
        episodes=(),
    )
    base.update(overrides)
    return PollSubject(**base)


def _gap(**overrides) -> EpisodeAir:
    base = dict(air_date=date(2026, 1, 1), downloaded=False, watched=False, season=1)
    base.update(overrides)
    return EpisodeAir(**base)


class _Source:
    def __init__(self, episode: int = 1) -> None:
        self.episode = episode
        self.seasons: list[list[int]] = []
        self.pages = 0

    def discover(self, query, preferences):
        del preferences
        self.seasons.append(list(query.seasons))
        return [
            SourceResult(
                title=query.title,
                year=query.year,
                media_type="series",
                ref=f"s{season}",
                url=f"https://wawa.test/s{season}",
                season=season,
                quality="1080p",
                language="MULTI",
            )
            for season in query.seasons
        ]

    def get_links(self, ref: SourceRef, *, hoster_preference=None, profile=None, **_kwargs) -> list[LinkCandidate]:
        del hoster_preference, profile
        self.pages += 1
        season = int(ref.ref[1:])
        return [
            LinkCandidate(
                url=f"https://rapidgator.net/file/{season}",
                kind="ddl_protected",
                hoster="rapidgator",
                external_id=f"rg-{season}-{self.pages}",
                season=season,
                episode=self.episode,
            ),
            LinkCandidate(
                url=f"https://1fichier.com/file/{season}",
                kind="ddl_protected",
                hoster="1fichier",
                external_id=f"1f-{season}-{self.pages}",
                season=season,
                episode=self.episode,
            ),
        ]

    def close(self) -> None:
        return None


def _series(db, **overrides) -> Media:
    values = dict(
        type="series",
        title="North Harbor",
        year=2024,
        tmdb_id=4242,
        followed=True,
        on_hold=False,
        new_episode_policy="auto",
        series_status="returning",
        pref_quality="HD",
        pref_language="VFF",
    )
    values.update(overrides)
    media = Media(**values)
    db.add(media)
    db.flush()
    return media


def _episode(db, media: Media, **overrides) -> MediaItem:
    values = dict(
        media_id=media.id,
        season=1,
        episode=4,
        title="Quai",
        air_date=date(2026, 1, 8),
        state="missing",
        downloaded=False,
        watched=False,
    )
    values.update(overrides)
    item = MediaItem(**values)
    db.add(item)
    db.flush()
    return item


def _link(season: int = 1, episode: int = 4, hoster: str = "rapidgator", external_id: str = "ext") -> LinkCandidate:
    return LinkCandidate(
        url=f"https://{hoster}.example/{external_id}",
        kind="ddl_protected",
        hoster=hoster,
        external_id=external_id,
        season=season,
        episode=episode,
    )


def _provider_id(db) -> int:
    return db.scalar(select(Provider.id).where(Provider.type == "wawacity"))


def _pending(db, item_id: int) -> int:
    return db.scalar(
        select(func.count())
        .select_from(Download)
        .where(Download.media_item_id == item_id, Download.state == "pending")
    )


def test_old_unwatched_gap_is_selected_and_queued(client, app):
    del client
    media = Media(type="series", title="North Harbor", followed=True, new_episode_policy="auto")
    old = MediaItem(season=1, episode=4, air_date=date(2025, 3, 1))
    watched = MediaItem(season=1, episode=1, air_date=date(2025, 1, 1), watched=True)
    future = MediaItem(season=1, episode=5, air_date=date(2026, 12, 1))
    undated = MediaItem(season=1, episode=6, air_date=None)
    assert {(slot.season, slot.episode) for slot in expected_slots(
        media, [old, watched, future, undated], today=TODAY, window_days=3, force=False
    )} == {(1, 4)}

    db = app.state.session_factory()
    try:
        stored = _series(db)
        item = _episode(db, stored, air_date=date(2025, 3, 1))
        db.commit()
        queued, proposed, count = _apply(
            db,
            stored,
            [item],
            [expected_slots(stored, [item], today=TODAY, window_days=3, force=False)[0]],
            [_link()],
            _provider_id(db),
            ["rapidgator"],
            NOW,
        )
        assert (queued, proposed, count) == (1, 0, 1)
        db.flush()
        assert _pending(db, item.id) == 1
    finally:
        db.close()


def test_watched_episode_is_not_queued(client, app):
    del client
    db = app.state.session_factory()
    try:
        media = _series(db)
        item = _episode(db, media, watched=True, air_date=date(2025, 3, 1))
        db.commit()
        assert expected_slots(media, [item], today=TODAY, window_days=3, force=False) == []
        queued, _proposed, _count = _apply(
            db,
            media,
            [item],
            [Slot(1, 4, item.id)],
            [_link(external_id="watched")],
            _provider_id(db),
            ["rapidgator"],
            NOW,
        )
        assert queued == 0
        assert _pending(db, item.id) == 0
    finally:
        db.close()


def test_done_job_with_a_missing_file_is_queued_once(client, app):
    del client
    db = app.state.session_factory()
    try:
        media = _series(db)
        item = _episode(db, media, air_date=date(2025, 3, 1))
        done_at = NOW - timedelta(days=40)
        db.add(
            Download(
                media_item_id=item.id,
                state="done",
                created_at=done_at,
                finished_at=done_at,
            )
        )
        db.commit()
        slot_list = expected_slots(media, [item], today=TODAY, window_days=3, force=False)
        assert [(slot.season, slot.episode) for slot in slot_list] == [(1, 4)]
        first, _, _ = _apply(
            db, media, [item], slot_list, [_link(external_id="once")], _provider_id(db), ["rapidgator"], NOW
        )
        db.flush()
        second, _, _ = _apply(
            db,
            media,
            [item],
            slot_list,
            [_link(external_id="twice")],
            _provider_id(db),
            ["rapidgator"],
            NOW + timedelta(hours=1),
        )
        assert first == 1
        assert second == 0
        pending = db.scalar(select(Download).where(Download.media_item_id == item.id, Download.state == "pending"))
        pending.state = "done"
        pending.finished_at = NOW + timedelta(hours=2)
        db.commit()
        third, _, _ = _apply(
            db,
            media,
            [item],
            slot_list,
            [_link(external_id="thrice")],
            _provider_id(db),
            ["rapidgator"],
            NOW + timedelta(days=1),
        )
        assert third == 0
        assert _pending(db, item.id) == 0
    finally:
        db.close()


def test_a_later_disappearance_may_be_tried_once(client, app):
    del client
    db = app.state.session_factory()
    try:
        media = _series(db)
        item = _episode(db, media)
        first_at = NOW - timedelta(days=40)
        second_at = NOW - timedelta(days=10)
        for moment in (first_at, second_at):
            db.add(Download(media_item_id=item.id, state="done", created_at=moment, finished_at=moment))
        db.add(
            LibraryFile(
                path="/tmp/north-harbor-gone.mkv",
                parsed_title="North Harbor",
                media_type="series",
                media_id=media.id,
                media_item_id=item.id,
                season=1,
                episode=4,
                status="missing",
                missing_at=NOW,
            )
        )
        db.commit()
        slot_list = expected_slots(media, [item], today=TODAY, window_days=3, force=False)
        queued, _, _ = _apply(
            db, media, [item], slot_list, [_link(external_id="again")], _provider_id(db), ["rapidgator"], NOW
        )
        db.flush()
        again, _, _ = _apply(
            db,
            media,
            [item],
            slot_list,
            [_link(external_id="again-2")],
            _provider_id(db),
            ["rapidgator"],
            NOW + timedelta(minutes=5),
        )
        assert queued == 1
        assert again == 0
    finally:
        db.close()


def test_hold_and_propose_do_not_queue(client, app):
    del client
    old = MediaItem(season=1, episode=4, air_date=date(2025, 3, 1))
    paused = Media(type="series", title="Pause", followed=True, on_hold=True, new_episode_policy="auto")
    proposed = Media(type="series", title="Propose", followed=True, new_episode_policy="propose")
    assert expected_slots(paused, [old], today=TODAY, window_days=3, force=True) == []
    assert expected_slots(proposed, [old], today=TODAY, window_days=3, force=False) == []

    db = app.state.session_factory()
    try:
        held = _series(db, title="Pause", on_hold=True)
        held_item = _episode(db, held)
        offered = _series(db, title="Propose", new_episode_policy="propose")
        offered_item = _episode(db, offered)
        db.commit()
        held_queued, _, _ = _apply(
            db,
            held,
            [held_item],
            [Slot(1, 4, held_item.id)],
            [_link(external_id="hold")],
            _provider_id(db),
            ["rapidgator"],
            NOW,
        )
        offer_queued, offer_proposed, _ = _apply(
            db,
            offered,
            [offered_item],
            [Slot(1, 4, offered_item.id)],
            [_link(external_id="propose")],
            _provider_id(db),
            ["rapidgator"],
            NOW,
        )
        assert held_queued == 0
        assert offer_queued == 0
        assert offer_proposed == 1
        assert _pending(db, held_item.id) == 0
        assert _pending(db, offered_item.id) == 0
    finally:
        db.close()


def test_onefichier_is_stored_with_rapidgator(client, app):
    del client
    db = app.state.session_factory()
    try:
        media = _series(db)
        item = _episode(db, media, air_date=date(2025, 3, 1))
        db.commit()
        preference = parse_hoster_preference("rapidgator")
        queued, _, _ = _apply(
            db,
            media,
            [item],
            [Slot(1, 4, item.id)],
            [_link(external_id="rg-ok"), _link(hoster="1fichier", external_id="hidden")],
            _provider_id(db),
            preference,
            NOW,
        )
        assert queued == 1
        db.flush()
        hosters = set(db.scalars(select(Link.hoster).where(Link.media_item_id == item.id)).all())
        assert hosters == {"rapidgator", "1fichier"}
    finally:
        db.close()


def test_deleted_file_is_marked_on_check_and_queued_without_the_daily_scan(client, app):
    del client
    series = Path(app.state.settings.data_dir) / "media" / "Series"
    series.mkdir(parents=True)
    gone = series / "North Harbor (2024)" / "North Harbor (2024) - S01E04.mkv"
    db = app.state.session_factory()
    try:
        media = _series(db)
        item = _episode(
            db,
            media,
            downloaded=True,
            state="downloaded",
            file_path=str(gone),
            air_date=date(2025, 3, 1),
        )
        db.commit()
        allowed, reason = _gate(db, app.state.settings, media, [item], NOW)
        assert (allowed, reason) == (True, "missing_episode")
        source = _Source(episode=4)
        report = run_poll(
            db,
            media,
            _provider_id(db),
            source,
            app.state.settings,
            now=NOW,
            force=True,
        )
        db.refresh(item)
        assert report.queued == 1
        assert item.downloaded is False
        assert item.file_path is None
        assert item.watched is False
        assert item.state == "available"
        recorded = db.scalar(select(LibraryFile).where(LibraryFile.media_item_id == item.id))
        assert recorded is not None
        assert recorded.status == "missing"
        assert recorded.missing_at is not None
        assert _pending(db, item.id) == 1
        assert source.pages <= 4
        urls = db.scalars(select(Link.url)).all()
        assert any("1fichier" in url for url in urls)
        assert any("rapidgator" in url for url in urls)
        again = run_poll(
            db,
            media,
            _provider_id(db),
            source,
            app.state.settings,
            now=NOW + timedelta(hours=13),
            force=True,
        )
        assert again.queued == 0
        assert _pending(db, item.id) == 1
    finally:
        db.close()


def test_another_filename_on_disk_is_not_queued(client, app):
    del client
    series = Path(app.state.settings.data_dir) / "media" / "Series" / "North Harbor (2024)"
    series.mkdir(parents=True)
    video = series / "North.Harbor.S01E04.mkv"
    video.write_bytes(b"v" * 2048)
    gone = series / "North Harbor (2024) - S01E04.mkv"
    db = app.state.session_factory()
    try:
        media = _series(db)
        item = _episode(
            db,
            media,
            downloaded=True,
            state="downloaded",
            file_path=str(gone),
            air_date=date(2025, 3, 1),
        )
        db.commit()
        report = run_poll(
            db,
            media,
            _provider_id(db),
            _Source(),
            app.state.settings,
            now=NOW,
            force=True,
        )
        db.refresh(item)
        assert report.queued == 0
        assert item.downloaded is True
        assert item.watched is False
        assert Path(item.file_path) == video
        assert db.scalars(select(Download).where(Download.media_item_id == item.id)).all() == []
    finally:
        db.close()


def test_watched_deletion_is_marked_missing_and_not_queued(client, app):
    del client
    series = Path(app.state.settings.data_dir) / "media" / "Series"
    series.mkdir(parents=True)
    gone = series / "North Harbor (2024)" / "North Harbor (2024) - S01E04.mkv"
    db = app.state.session_factory()
    try:
        media = _series(db)
        item = _episode(
            db,
            media,
            downloaded=True,
            watched=True,
            state="downloaded",
            file_path=str(gone),
        )
        db.commit()
        report = run_poll(
            db, media, _provider_id(db), _Source(), app.state.settings, now=NOW, force=True
        )
        db.refresh(item)
        assert report.queued == 0
        assert item.watched is True
        assert item.downloaded is False
        assert db.scalars(select(Download)).all() == []
    finally:
        db.close()


def test_a_check_opens_at_most_four_seasons(client, app):
    del client
    db = app.state.session_factory()
    try:
        media = _series(db)
        for number, air in (
            (1, date(2020, 1, 1)),
            (2, date(2020, 6, 1)),
            (3, date(2021, 1, 1)),
            (4, date(2021, 6, 1)),
            (5, date(2022, 1, 1)),
            (6, date(2026, 10, 10)),
        ):
            _episode(db, media, season=number, episode=1, title=f"S{number}", air_date=air)
        db.commit()
        source = _Source()
        run_poll(db, media, _provider_id(db), source, app.state.settings, now=NOW, force=True)
        assert source.seasons
        opened = set(source.seasons[0])
        assert len(opened) <= 4
        assert 6 in opened
        assert 1 in opened
        assert source.pages <= 4
    finally:
        db.close()


def test_poll_gate_fills_auto_gaps_and_leaves_propose_on_the_window():
    kwargs = WINDOW
    assert should_poll_source(_subject(episodes=(_gap(),)), **kwargs) == (True, "missing_episode")
    assert should_poll_source(
        _subject(new_episode_policy="propose", episodes=(_gap(),)),
        **kwargs,
    ) == (False, "outside_air_window")
    assert should_poll_source(_subject(on_hold=True, episodes=(_gap(),)), **kwargs) == (False, "on_hold")
    assert should_poll_source(
        _subject(episodes=(_gap(watched=True),)),
        **kwargs,
    ) == (False, "outside_air_window")
    assert should_poll_source(
        _subject(episodes=(_gap(air_date=None),), next_air_date=None),
        **kwargs,
    ) == (True, "no_air_date")
    assert should_poll_source(
        _subject(episodes=(_gap(air_date=date(2026, 12, 1)),), next_air_date=date(2026, 12, 1)),
        **kwargs,
    ) == (False, "before_air_date")
    assert should_poll_source(
        _subject(last_source_poll_at=NOW - timedelta(hours=1), episodes=(_gap(),)),
        **kwargs,
    ) == (False, "interval")
    assert should_poll_source(
        _subject(series_status="ended", episodes=(_gap(),)),
        **kwargs,
    ) == (True, "missing_episode")
    assert should_poll_source(
        _subject(tracked_seasons=(2,), episodes=(_gap(season=1),)),
        **kwargs,
    ) == (False, "outside_air_window")
