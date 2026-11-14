from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from tests.conftest import login
from tests.test_video_integrity import _minimal_mkv  # noqa: I001

from mediatracker.core.filing_integrity import block_if_suspect
from mediatracker.core.pipeline import tick
from mediatracker.core.source_poll import _blocking_download
from mediatracker.db.models import Download, Event, LibraryFile, Media, MediaItem


def _truncated_mkv(tmp_path: Path) -> Path:
    data = _minimal_mkv()
    path = tmp_path / "incomplete" / "Fable.Trunc.mkv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data[:-1])
    return path


def test_block_if_suspect_marks_library_and_journal(client, app, tmp_path) -> None:
    login(client)
    source = _truncated_mkv(tmp_path)
    db = app.state.session_factory()
    try:
        media = Media(type="film", title="Fable", year=2026)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, season=None, episode=None, state="downloading")
        db.add(item)
        db.flush()
        row = Download(media_item_id=item.id, state="post_processing", filename=source.name, error=str(source))
        db.add(row)
        db.commit()
        now = datetime.now(timezone.utc)
        assert block_if_suspect(db, row, item, media, source, now)
        db.commit()
        library = db.scalar(select(LibraryFile).where(LibraryFile.path == str(source.resolve())))
        assert library is not None
        assert library.integrity_suspect is True
        event = db.scalar(select(Event).where(Event.type == "library.file_suspect"))
        assert event is not None
        assert row.state == "failed"
        assert row.error_code == "FILE_INTEGRITY"
        assert source.is_file()
    finally:
        db.close()


def test_library_review_lists_suspect_files(client, app, tmp_path) -> None:
    login(client)
    source = _truncated_mkv(tmp_path)
    db = app.state.session_factory()
    try:
        media = Media(type="film", title="Fable", year=2026)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, season=None, episode=None, state="downloading")
        db.add(item)
        db.flush()
        row = Download(media_item_id=item.id, state="post_processing", filename=source.name, error=str(source))
        db.add(row)
        db.commit()
        now = datetime.now(timezone.utc)
        block_if_suspect(db, row, item, media, source, now)
        db.commit()
    finally:
        db.close()
    review = client.get("/api/v1/library/review")
    assert review.status_code == 200, review.text
    suspects = review.json()["suspect_files"]
    assert len(suspects) == 1
    assert suspects[0]["name"] == "Fable.Trunc.mkv"
    assert suspects[0]["message"]


def test_file_integrity_failure_does_not_auto_redownload(client, app, tmp_path) -> None:
    """``_fail`` laisse ``next_retry_at`` vide ; le poll source ignore l'épisode tant qu'un job ``failed`` existe."""
    login(client)
    source = _truncated_mkv(tmp_path)
    db = app.state.session_factory()
    try:
        media = Media(type="film", title="Fable", year=2026, auto_download=True)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, season=None, episode=None, state="downloading", downloaded=False)
        db.add(item)
        db.flush()
        row = Download(media_item_id=item.id, state="post_processing", filename=source.name, error=str(source))
        db.add(row)
        db.commit()
        now = datetime.now(timezone.utc)
        assert block_if_suspect(db, row, item, media, source, now)
        db.commit()
        assert row.next_retry_at is None
        assert _blocking_download(db, item.id) is True
        for _ in range(3):
            tick(db, app.state.settings, now=now)
            db.commit()
        downloads = list(db.scalars(select(Download)))
        assert len(downloads) == 1
        assert downloads[0].state == "failed"
        assert downloads[0].error_code == "FILE_INTEGRITY"
    finally:
        db.close()


def test_complete_mkv_is_filed(client, app, tmp_path) -> None:
    from mediatracker.core.storage import incomplete_dir, storage_layout

    login(client)
    data = _minimal_mkv()
    db = app.state.session_factory()
    try:
        layout = storage_layout(db, app.state.settings)
        incomplete = incomplete_dir(layout)
        incomplete.mkdir(parents=True, exist_ok=True)
        source = incomplete / "Harbor.Complete.mkv"
        source.write_bytes(data)
        media = Media(type="film", title="Harbor", year=2026, tmdb_id=9001)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, season=None, episode=None, state="downloading")
        db.add(item)
        db.flush()
        row = Download(
            media_item_id=item.id,
            state="post_processing",
            filename=source.name,
            error=str(source),
            progress=100,
        )
        db.add(row)
        db.commit()
    finally:
        db.close()

    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=datetime.now(timezone.utc))
        db.commit()
        row = db.scalar(select(Download))
        assert row.state == "done"
        assert not source.is_file()
        library = db.scalar(select(LibraryFile))
        assert library is not None
        assert library.integrity_suspect is False
    finally:
        db.close()
