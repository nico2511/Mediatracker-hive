"""Validation bout en bout V1 (corbeille) et V2 (comblement des manquants).

Fichiers vidéo factices, noms fictifs, SQLite temporaire, intégrations simulées.
"""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from tests.conftest import csrf, login
from tests.test_a7_download_attempts import NOW as PIPELINE_NOW
from tests.test_a7_download_attempts import _advance_past_backoff
from tests.test_a8_failed_download_terminal import LONE_URL, _fail_job
from tests.test_fill_missing import _provider_id, _Source, run_poll
from tests.test_pipeline import FakeDownloader, _enable_debrid

from mediatracker.core.cleanup import maintenance, preview
from mediatracker.core.forget import TRACE_DELETED_VOLUNTARILY, is_voluntarily_deleted
from mediatracker.core.pipeline import tick
from mediatracker.core.pipeline.constants import MAX_ATTEMPTS
from mediatracker.core.source_poll import _blocking_download
from mediatracker.db.models import Download, Media, MediaItem, MediaSeason, TrashEntry

NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
LATER = NOW + timedelta(hours=25)


def _enable_cleanup(client) -> None:
    login(client)
    saved = client.patch(
        "/api/v1/settings",
        json={
            "values": {
                "auto_delete_days": 14,
                "cleanup_notice_hours": 24,
                "trash_retention_days": 7,
            }
        },
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text


def _video(root: Path, rel: str, payload: bytes = b"v") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload * max(1, 20 // len(payload)))
    return path


def _film(db, title: str, **media_kw) -> Media:
    row = Media(type="film", title=title, auto_delete=True, **media_kw)
    db.add(row)
    db.flush()
    return row


def _film_item(db, media: Media, path: Path, *, watched_at: datetime | None = None, keep: bool = False) -> MediaItem:
    row = MediaItem(
        media_id=media.id,
        title=media.title,
        state="downloaded",
        downloaded=True,
        watched=watched_at is not None,
        watched_at=watched_at,
        file_path=str(path.resolve()),
        keep=keep,
    )
    db.add(row)
    db.flush()
    return row


def _episode(
    db,
    media: Media,
    path: Path | None,
    *,
    season: int = 1,
    episode: int = 1,
    watched_at: datetime | None = None,
    keep: bool = False,
    traces: list | None = None,
) -> MediaItem:
    row = MediaItem(
        media_id=media.id,
        season=season,
        episode=episode,
        title=f"Épisode {episode}",
        state="downloaded" if path else "missing",
        downloaded=path is not None,
        watched=watched_at is not None,
        watched_at=watched_at,
        file_path=str(path.resolve()) if path else None,
        keep=keep,
        traces=traces or [],
        air_date=date(2025, 6, episode),
    )
    db.add(row)
    db.flush()
    return row


def test_v1_auto_manual_notice_restore_purge_and_keep(client, app):
    """V1 : auto après préavis, manuel, restauration, purge seule définitive."""
    _enable_cleanup(client)
    movies = Path(app.state.settings.data_dir) / "media" / "Films"
    old_watch = NOW - timedelta(days=20)
    auto_path = _video(movies, "Nebuleuse/Nebuleuse.mkv")

    db = app.state.session_factory()
    try:
        auto_media = _film(db, "Nebuleuse")
        auto_item = _film_item(db, auto_media, auto_path, watched_at=old_watch)
        db.commit()
        auto_media_id = auto_media.id
        auto_item_id = auto_item.id
    finally:
        db.close()

    db = app.state.session_factory()
    try:
        _due, upcoming = preview(db, app.state.settings, now=NOW)
        assert upcoming and not _due
        assert "Nebuleuse" in {row.plan.media.title for row in upcoming}
        maintenance(db, app.state.settings, now=NOW)
        assert auto_path.is_file()
        maintenance(db, app.state.settings, now=LATER)
        assert not auto_path.is_file()
        auto_item = db.get(MediaItem, auto_item_id)
        assert auto_item.downloaded is False and auto_item.watched is True
        entry = db.scalar(select(TrashEntry).where(TrashEntry.media_item_id == auto_item_id))
        assert entry is not None and entry.reason == "auto"
        assert Path(entry.trash_path).is_file()
        entry_id = entry.id
    finally:
        db.close()

    restored = client.post(f"/api/v1/trash/{entry_id}/restore", headers=csrf(client))
    assert restored.status_code == 200, restored.text
    assert auto_path.is_file()
    db = app.state.session_factory()
    try:
        auto_item = db.get(MediaItem, auto_item_id)
        assert auto_item.downloaded is True
        assert auto_item.file_path == str(auto_path.resolve())
    finally:
        db.close()

    removed = client.post(
        "/api/v1/cleanup/files",
        json={"media_id": auto_media_id, "scope": "episode", "item_id": auto_item_id, "confirm": True},
        headers=csrf(client),
    )
    assert removed.status_code == 200, removed.text
    assert not auto_path.is_file()
    new_entry = removed.json()["entries"][0]["id"]
    db = app.state.session_factory()
    try:
        row = db.get(TrashEntry, new_entry)
        row.purge_after = NOW - timedelta(days=1)
        db.commit()
        maintenance(db, app.state.settings, now=NOW)
        db.refresh(row)
        assert row.purged_at is not None
        assert not Path(row.trash_path).exists()
    finally:
        db.close()


def test_v1_keep_at_fiche_season_and_episode_skips_auto(client, app):
    """V1 : « Garder » sur fiche, saison ou épisode — jamais de corbeille auto."""
    _enable_cleanup(client)
    movies = Path(app.state.settings.data_dir) / "media" / "Films"
    series = Path(app.state.settings.data_dir) / "media" / "Series"
    old_watch = NOW - timedelta(days=30)
    db = app.state.session_factory()
    try:
        by_fiche = _film(db, "Atlas Veille", keep=True)
        fiche_path = _video(movies, "Atlas/Atlas.mkv")
        _film_item(db, by_fiche, fiche_path, watched_at=old_watch)

        by_season = Media(type="series", title="Saison Gardée", auto_delete=True)
        db.add(by_season)
        db.flush()
        season_path = _video(series, "Saison/Season 01/a.mkv")
        _episode(db, by_season, season_path, watched_at=old_watch)
        db.add(MediaSeason(media_id=by_season.id, number=1, keep=True))

        by_episode = Media(type="series", title="Épisode Gardé", auto_delete=True)
        db.add(by_episode)
        db.flush()
        ep_path = _video(series, "Episode/Season 01/a.mkv")
        _episode(db, by_episode, ep_path, watched_at=old_watch, keep=True)
        db.commit()
    finally:
        db.close()

    db = app.state.session_factory()
    try:
        maintenance(db, app.state.settings, now=LATER)
        assert fiche_path.is_file() and season_path.is_file() and ep_path.is_file()
    finally:
        db.close()


def test_v1_active_download_never_auto_trash_candidate(client, app):
    """V1 : un épisode avec téléchargement actif n'est pas candidat au nettoyage auto."""
    _enable_cleanup(client)
    data = Path(app.state.settings.data_dir)
    series = data / "media" / "Series"
    path = _video(series, "Flux Mare/Season 01/Flux.Mare.S01E01.mkv")
    old_watch = NOW - timedelta(days=20)
    db = app.state.session_factory()
    try:
        show = Media(type="series", title="Flux Mare", auto_delete=True)
        db.add(show)
        db.flush()
        item = _episode(db, show, path, watched_at=old_watch)
        db.add(Download(media_item_id=item.id, state="downloading", filename="Flux.Mare.S01E01.mkv", created_at=NOW))
        db.commit()
        _due, upcoming = preview(db, app.state.settings, now=NOW)
        labels = {row.plan.label for row in upcoming + _due}
        assert not any("Flux Mare" in label for label in labels)
        maintenance(db, app.state.settings, now=LATER)
        assert path.is_file()
    finally:
        db.close()


def test_v2_trashed_episode_not_gap_filled(client, app):
    """V2 : épisode mis à la corbeille volontairement — pas de nouveau téléchargement auto."""
    _enable_cleanup(client)
    data = Path(app.state.settings.data_dir)
    series = data / "media" / "Series"
    path = _video(series, "Rivage Nord (2026)/Rivage.Nord.S01E02.mkv")
    db = app.state.session_factory()
    try:
        media = Media(
            type="series",
            title="Rivage Nord",
            year=2026,
            tmdb_id=88001,
            followed=True,
            new_episode_policy="auto",
        )
        db.add(media)
        db.flush()
        item = _episode(
            db,
            media,
            path,
            episode=2,
            watched_at=NOW - timedelta(days=2),
            traces=[{"kind": TRACE_DELETED_VOLUNTARILY, "at": NOW.isoformat()}],
        )
        item.downloaded = False
        item.file_path = None
        item.watched = True
        db.commit()
        assert is_voluntarily_deleted(item)
        report = run_poll(
            db,
            media,
            _provider_id(db),
            _Source(episode=2),
            app.state.settings,
            now=NOW + timedelta(days=1),
            force=True,
        )
        assert report.queued == 0
        assert db.scalars(select(Download).where(Download.media_item_id == item.id)).all() == []
    finally:
        db.close()


def test_v2_failed_terminal_until_explicit_retry(client, app):
    """V2 : état En erreur sans reprise auto ; réessai uniquement sur action explicite."""
    login(client)
    _enable_debrid(app)
    download_id, debrid = _fail_job(client, app)
    db = app.state.session_factory()
    try:
        row = db.get(Download, download_id)
        assert row.state == "failed"
        failed_at = row.finished_at
        item = db.get(MediaItem, row.media_item_id)
        media = db.get(Media, item.media_id)
        item.state = "missing"
        item.downloaded = False
        item.air_date = date(2020, 1, 1)
        media.followed = True
        media.new_episode_policy = "auto"
        db.commit()
        assert _blocking_download(db, item.id) is True
        tick(db, app.state.settings, now=LATER, debrid=debrid, downloader=FakeDownloader())
        db.refresh(row)
        assert row.state == "failed"
        assert row.finished_at == failed_at
        assert len(debrid.seen) == MAX_ATTEMPTS
    finally:
        db.close()

    retried = client.post(f"/api/v1/downloads/{download_id}/retry", headers=csrf(client))
    assert retried.status_code == 200, retried.text
    assert retried.json()["state"] == "pending"

    db = app.state.session_factory()
    try:
        moment = _advance_past_backoff(PIPELINE_NOW, 1)
        tick(db, app.state.settings, now=moment, debrid=debrid, downloader=FakeDownloader())
        assert debrid.seen[-1] == LONE_URL
    finally:
        db.close()
