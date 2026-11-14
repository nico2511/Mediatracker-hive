"""F15: auto-trash by default, Garder inheritance, migration, manual confirm."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from tests.conftest import csrf, login

from mediatracker.core.cleanup import maintenance, preview
from mediatracker.core.keep import MIGRATION_FLAG_KEY, effective_keep, migrate_cleanup_auto_default, season_keep_map
from mediatracker.db.models import Media, MediaItem, MediaSeason, Setting


def _settings(client):
    login(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"auto_delete_days": 14, "cleanup_notice_hours": 24, "trash_retention_days": 7}},
        headers=csrf(client),
    )


def _film(db, title: str, **kwargs):
    row = Media(type="film", title=title, auto_delete=kwargs.pop("auto_delete", False), **kwargs)
    db.add(row)
    db.flush()
    return row


def _item(db, media, path: Path, *, season=None, episode=None, watched_at=None, keep=False):
    row = MediaItem(
        media_id=media.id,
        season=season,
        episode=episode,
        title=media.title,
        state="downloaded",
        downloaded=True,
        watched=True,
        watched_at=watched_at,
        file_path=str(path),
        keep=keep,
    )
    db.add(row)
    db.flush()
    return row


def test_watched_goes_to_trash_after_notice(client, app):
    _settings(client)
    now = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
    movies = Path(app.state.settings.data_dir) / "media" / "Films"
    path = movies / "Vu/Vu.mkv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * 20)
    db = app.state.session_factory()
    try:
        media = _film(db, "Vu")
        _item(db, media, path, watched_at=now - timedelta(days=20))
        db.commit()
        maintenance(db, app.state.settings, now=now)
        assert path.is_file()
        maintenance(db, app.state.settings, now=now + timedelta(hours=25))
        assert not path.is_file()
    finally:
        db.close()


def test_keep_at_media_season_and_episode(client, app):
    _settings(client)
    now = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
    series = Path(app.state.settings.data_dir) / "media" / "Series"
    old = now - timedelta(days=30)
    db = app.state.session_factory()
    try:
        by_media = Media(type="series", title="Par fiche", keep=True)
        db.add(by_media)
        db.flush()
        m_path = series / "M/Season 01/a.mkv"
        m_path.parent.mkdir(parents=True, exist_ok=True)
        m_path.write_bytes(b"m" * 10)
        _item(db, by_media, m_path, season=1, episode=1, watched_at=old)
        db.commit()
        maintenance(db, app.state.settings, now=now + timedelta(hours=25))
        assert m_path.is_file()

        by_season = Media(type="series", title="Par saison")
        db.add(by_season)
        db.flush()
        s_path = series / "S/Season 01/a.mkv"
        s_path.parent.mkdir(parents=True, exist_ok=True)
        s_path.write_bytes(b"s" * 10)
        _item(db, by_season, s_path, season=1, episode=1, watched_at=old)
        db.add(MediaSeason(media_id=by_season.id, number=1, keep=True))
        db.commit()
        maintenance(db, app.state.settings, now=now + timedelta(hours=25))
        assert s_path.is_file()

        by_episode = Media(type="series", title="Par épisode")
        db.add(by_episode)
        db.flush()
        e_path = series / "E/Season 01/a.mkv"
        e_path.parent.mkdir(parents=True, exist_ok=True)
        e_path.write_bytes(b"e" * 10)
        _item(db, by_episode, e_path, season=1, episode=1, watched_at=old, keep=True)
        db.commit()
        maintenance(db, app.state.settings, now=now + timedelta(hours=25))
        assert e_path.is_file()

        item = db.scalar(select(MediaItem).where(MediaItem.media_id == by_episode.id))
        item.keep = False
        db.commit()
        later = now + timedelta(days=40)
        maintenance(db, app.state.settings, now=later)
        maintenance(db, app.state.settings, now=later + timedelta(hours=25))
        assert not e_path.is_file()
    finally:
        db.close()


def test_migration_does_not_enable_keep_and_is_idempotent(client, app):
    _settings(client)
    db = app.state.session_factory()
    try:
        db.delete(db.get(Setting, MIGRATION_FLAG_KEY))
        db.commit()
        legacy = _film(db, "Ancienne", auto_delete=False)
        already = _film(db, "Déjà", auto_delete=False, keep=True)
        db.commit()
        first = migrate_cleanup_auto_default(db)
        db.commit()
        assert first == 0
        assert legacy.keep is False
        assert already.keep is True
        assert db.get(Setting, MIGRATION_FLAG_KEY).value == "true"
        second = migrate_cleanup_auto_default(db)
        db.commit()
        assert second == 0
        assert db.scalar(select(Media).where(Media.title == "Ancienne")).keep is False
    finally:
        db.close()


def test_effective_keep_inheritance():
    media = Media(type="series", title="Héritage", keep=False)
    season = MediaSeason(media_id=1, number=2, keep=True)
    episode = MediaItem(media_id=1, season=2, episode=3, keep=False)
    seasons = {2: season}
    assert effective_keep(media, 2, episode, seasons)
    media.keep = True
    assert effective_keep(media, 2, episode, seasons)
    media.keep = False
    season.keep = False
    episode.keep = True
    assert effective_keep(media, 2, episode, seasons)


def test_preview_candidates_respect_keep_inheritance(client, app):
    _settings(client)
    now = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
    old = now - timedelta(days=30)
    series = Path(app.state.settings.data_dir) / "media" / "Series"
    db = app.state.session_factory()
    try:
        candidate = Media(type="series", title="Candidat")
        db.add(candidate)
        db.flush()
        cand_path = series / "Cand/Season 01/e01.mkv"
        cand_path.parent.mkdir(parents=True, exist_ok=True)
        cand_path.write_bytes(b"c" * 12)
        _item(db, candidate, cand_path, season=1, episode=1, watched_at=old)

        by_season = Media(type="series", title="Saison gardée")
        db.add(by_season)
        db.flush()
        season_path = series / "Saison/Season 01/e01.mkv"
        season_path.parent.mkdir(parents=True, exist_ok=True)
        season_path.write_bytes(b"s" * 12)
        _item(db, by_season, season_path, season=1, episode=1, watched_at=old, keep=False)
        db.add(MediaSeason(media_id=by_season.id, number=1, keep=True))

        by_fiche = Media(type="series", title="Fiche gardée", keep=True)
        db.add(by_fiche)
        db.flush()
        fiche_path = series / "Fiche/Season 01/e01.mkv"
        fiche_path.parent.mkdir(parents=True, exist_ok=True)
        fiche_path.write_bytes(b"f" * 12)
        _item(db, by_fiche, fiche_path, season=1, episode=1, watched_at=old, keep=False)
        db.commit()

        due, upcoming = preview(db, app.state.settings, now=now + timedelta(hours=25))
        paths = {str(planned.path) for group in due + upcoming for planned in group.plan.files}
        assert str(cand_path) in paths
        assert str(season_path) not in paths
        assert str(fiche_path) not in paths
        cand_item = db.scalar(select(MediaItem).where(MediaItem.file_path == str(cand_path)))
        assert not effective_keep(candidate, 1, cand_item, season_keep_map(db, candidate.id))
    finally:
        db.close()


def test_manual_trash_confirm_only_when_kept(client, app):
    _settings(client)
    now = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
    movies = Path(app.state.settings.data_dir) / "media" / "Films"
    free = movies / "Libre/Libre.mkv"
    held = movies / "Gardé/Gardé.mkv"
    for p in (free, held):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"z" * 8)
    db = app.state.session_factory()
    try:
        free_media = _film(db, "Libre")
        held_media = _film(db, "Gardé", keep=True)
        free_item = _item(db, free_media, free, watched_at=now)
        held_item = _item(db, held_media, held, watched_at=now)
        db.commit()
        free_id, held_id = free_media.id, held_media.id
        free_item_id, held_item_id = free_item.id, held_item.id
    finally:
        db.close()

    refused = client.post(
        "/api/v1/cleanup/files",
        json={"media_id": held_id, "scope": "episode", "item_id": held_item_id, "confirm": False},
        headers=csrf(client),
    )
    assert refused.status_code == 422
    assert held.is_file()

    ok = client.post(
        "/api/v1/cleanup/files",
        json={"media_id": free_id, "scope": "episode", "item_id": free_item_id, "confirm": False},
        headers=csrf(client),
    )
    assert ok.status_code == 200, ok.text
    assert not free.is_file()
    assert held.is_file()
