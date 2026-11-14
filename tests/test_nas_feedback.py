"""NAS feedback: AllDebrid call budget, catch-up, Wawacity tokens, folder rename."""

import logging
from datetime import date, datetime, timezone

from tests.conftest import login
from tests.test_tracking import _series

from mediatracker.core.library_plan import list_folders, rename_folder, undo_rename
from mediatracker.core.pipeline import tick
from mediatracker.core.polling import EpisodeAir, should_poll_source
from mediatracker.core.precheck import clear_known_files_cache, load_known_files
from mediatracker.core.source_poll import expected_slots, queues_download
from mediatracker.db.models import Media, MediaItem
from mediatracker.providers.sources.wawacity_html import parse_heading
from mediatracker.security.logging import configure_logging


def test_known_files_are_cached_across_clients():
    clear_known_files_cache()

    class Debrid:
        type = "alldebrid"

        def __init__(self) -> None:
            self.calls = 0

        def known_files(self) -> list:
            self.calls += 1
            return []

    first, second = Debrid(), Debrid()
    load_known_files(first, now=10)
    load_known_files(second, now=20)
    assert first.calls == 1
    assert second.calls == 0
    load_known_files(second, now=400)
    assert second.calls == 1
    clear_known_files_cache()


def test_idle_ticks_do_not_ask_alldebrid(client, app):
    del client
    clear_known_files_cache()

    class Debrid:
        type = "alldebrid"

        def __init__(self) -> None:
            self.calls = 0

        def known_files(self) -> list:
            self.calls += 1
            return []

    debrid = Debrid()
    db = app.state.session_factory()
    try:
        for _ in range(5):
            tick(
                db,
                app.state.settings,
                now=datetime(2026, 10, 11, tzinfo=timezone.utc),
                debrid=debrid,
                downloader=object(),
            )
    finally:
        db.close()
    assert debrid.calls == 0


def test_repeated_download_reads_stay_on_the_database(client):
    login(client)
    for _ in range(5):
        page = client.get("/api/v1/downloads")
        assert page.status_code == 200
        assert "items" in page.json()


def test_httpx_logs_are_warnings(tmp_path):
    configure_logging("INFO", tmp_path)
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING


def test_catch_up_selects_unwatched_episodes_not_on_disk():
    media = Media(type="series", title="Série", new_episode_policy="auto", followed=True)
    today = date(2026, 10, 11)
    items = [
        MediaItem(season=2, episode=1, air_date=date(2026, 1, 1), watched=True),
        MediaItem(season=2, episode=2, air_date=date(2026, 1, 8), downloaded=True),
        MediaItem(season=2, episode=3, air_date=date(2026, 1, 15), file_path="/data/ep.mkv"),
        MediaItem(season=2, episode=4, air_date=date(2026, 10, 1)),
        MediaItem(season=2, episode=5, air_date=date(2026, 12, 1)),
        MediaItem(season=2, episode=6, air_date=None),
    ]
    slots = expected_slots(media, items, today=today, window_days=3, force=False, catch_up=True)
    assert {(slot.season, slot.episode) for slot in slots} == {(2, 4), (2, 6)}
    quiet = expected_slots(media, items, today=today, window_days=3, force=False, catch_up=False)
    # Episode 4 aired ten days ago: automatic fill ignores the 3-day window.
    # Episode 6 has no air date and stays on the one-shot catch-up only.
    assert {(slot.season, slot.episode) for slot in quiet} == {(2, 4)}


def test_tracked_seasons_limit_catch_up_and_backlog_queues():
    media = Media(type="series", title="Série", new_episode_policy="propose", tracked_seasons=[1])
    today = date(2026, 10, 11)
    items = [
        MediaItem(season=1, episode=1, air_date=date(2026, 1, 1)),
        MediaItem(season=2, episode=1, air_date=date(2026, 2, 1)),
    ]
    slots = expected_slots(media, items, today=today, window_days=3, force=False, catch_up=True)
    assert {(slot.season, slot.episode) for slot in slots} == {(1, 1)}
    assert queues_download("propose", True) is True
    assert queues_download("propose", False) is False
    assert queues_download("auto", False) is True


def test_catch_up_polls_outside_the_air_window():
    now = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
    subject = _series(
        new_episode_policy="auto",
        catch_up=True,
        last_source_poll_at=now,
        next_air_date=date(2026, 1, 1),
        episodes=(EpisodeAir(date(2026, 1, 1), False),),
    )
    kwargs = dict(now=now, interval=__import__("datetime").timedelta(hours=12), ongoing_only=True, window_days=3)
    assert should_poll_source(subject, **kwargs) == (True, "catch_up")
    asked = _series(
        new_episode_policy="propose",
        backlog=True,
        last_source_poll_at=now,
        next_air_date=date(2026, 1, 1),
        episodes=(EpisodeAir(date(2026, 1, 1), False),),
    )
    assert should_poll_source(asked, **kwargs) == (True, "backlog")
    held = _series(on_hold=True, catch_up=True, new_episode_policy="auto")
    assert should_poll_source(held, **kwargs) == (False, "on_hold")


def test_heading_drops_the_doubled_dash_before_the_version():
    heading = parse_heading("Héros Démo - Saison 2 - VF")
    assert heading.title == "Héros Démo"
    assert heading.season == 2
    assert heading.language == "VF"
    leftover = parse_heading("Héros Démo - - VF")
    assert leftover.title == "Héros Démo"
    assert " - - " not in leftover.title
    kept = parse_heading("HD Forever")
    assert kept.title == "HD Forever"


def test_webrrip_truefrench_matches_hd_vff():
    heading = parse_heading("Jouet Démo 5 (2026) [WEBRIP] - TRUEFRENCH")
    assert heading.title == "Jouet Démo 5"
    assert heading.year == 2026
    assert heading.quality == "HD"
    assert heading.language == "VFF"
    mixed = parse_heading("[WEB-DL 1080p] - MULTI (TRUEFRENCH)")
    assert mixed.quality == "1080p"
    assert mixed.language == "MULTI"


def test_folder_rename_keeps_sidecars_and_can_be_undone(tmp_path):
    root = tmp_path / "Films"
    folder = root / "Jouet Démo 5 [tmdbid-99]"
    folder.mkdir(parents=True)
    video = folder / "toy.mkv"
    video.write_bytes(b"video")
    (folder / "toy.srt").write_text("sub", encoding="utf-8")
    (folder / "poster.jpg").write_bytes(b"img")
    rows = list_folders(root, tmp_path / "Series")
    assert rows[0].tmdb_id == 99
    assert rows[0].media_type == "film"
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from mediatracker.db.base import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'rename.db'}")
    Base.metadata.create_all(engine)
    db = Session(engine)
    try:
        logged = rename_folder(db, folder, root=root, media_type="film", title="Jouet Démo 5", year=2026)
        db.commit()
        dest = root / "Jouet Démo 5 (2026)" / "Jouet Démo 5.mkv"
        assert dest.is_file()
        assert (dest.parent / "Jouet Démo 5.srt").is_file()
        assert (dest.parent / "poster.jpg").is_file()
        undo_rename(db, logged.id)
        db.commit()
        assert video.is_file()
    finally:
        db.close()
