"""Strict title matching, the release filename, and a backed-up database reset."""

from datetime import datetime, timezone
from pathlib import Path

from tests.conftest import csrf, login

from mediatracker.core.library_plan import rename_folder, undo_rename
from mediatracker.core.source_select import select_variants
from mediatracker.db.models import LibraryFile, Media, MediaItem
from mediatracker.providers.base import EpisodeQuery, SourcePreferences, SourceResult
from mediatracker.providers.sources.comet import select_streams


def _page(title: str, year: int | None, ref: str) -> SourceResult:
    return SourceResult(
        title=title,
        year=year,
        media_type="film",
        ref=ref,
        url=f"https://example.test/{ref}",
        quality="1080p",
        language="MULTI",
    )


def test_ensorceleuses_does_not_take_pitch_black():
    pages = [
        _page("Pitch Black", 2000, "pitch"),
        _page("Sorcières Démo 2", None, "missing-year"),
        _page("Practical Magic 2", 2026, "magic"),
        _page("Sorcières Démo", 1998, "first"),
    ]
    chosen = select_variants(
        pages,
        title="Sorcières Démo 2",
        original_title="Practical Magic 2",
        year=2026,
        media_type="film",
        seasons=set(),
        quality="1080p",
        language="MULTI",
        allow_fallback=True,
        quality_fallback=("1080p", "HD"),
        language_fallback=("MULTI", "VFF"),
    )
    assert [item.ref for item in chosen] == ["magic"]

    pitch = {
        "streams": [
            {
                "name": "Comet",
                "description": "📄 Pitch.Black.2000.MULTi.1080p.mkv\n🇫🇷",
                "infoHash": "a" * 40,
                "fileIdx": 0,
                "behaviorHints": {
                    "filename": "Pitch.Black.2000.MULTi.1080p.mkv",
                    "videoSize": 2_000_000_000,
                },
            },
            {
                "name": "Comet",
                "description": "📄 Practical.Magic.2.2026.MULTi.VF2.1080p.mkv\n🇫🇷",
                "infoHash": "b" * 40,
                "fileIdx": 0,
                "behaviorHints": {
                    "filename": "Practical.Magic.2.2026.MULTi.VF2.1080p.WEBRip.mkv",
                    "videoSize": 2_000_000_000,
                },
            },
        ]
    }
    query = EpisodeQuery(
        title="Sorcières Démo 2",
        original_title="Practical Magic 2",
        year=2026,
        media_type="film",
        imdb_id="tt0000001",
    )
    kept = select_streams(pitch, query, SourcePreferences(quality="1080p", language="MULTI"))
    assert [item.filename for item in kept] == ["Practical.Magic.2.2026.MULTi.VF2.1080p.WEBRip.mkv"]


def test_original_name_survives_a_rename_and_undo(tmp_path, client, app):
    del client
    films = tmp_path / "Films"
    folder = films / "Practical Magic 2"
    folder.mkdir(parents=True)
    release = "Practical.Magic.2.2026.MULTi.VF2.1080p.WEBRip.mkv"
    (folder / release).write_bytes(b"video")
    db = app.state.session_factory()
    try:
        row = LibraryFile(
            path=str((folder / release).resolve()),
            size=5,
            parsed_title="Practical Magic 2",
            parsed_year=2026,
            media_type="film",
            original_name=release,
            file_source="wawacity",
            status="matched",
        )
        db.add(row)
        db.commit()
        renamed = rename_folder(
            db,
            folder,
            root=films,
            media_type="film",
            title="Sorcières Démo 2",
            year=2026,
            now=datetime(2026, 10, 13, tzinfo=timezone.utc),
        )
        db.commit()
        assert renamed is not None
        move = renamed.moves[0]
        assert move["original_name"] == release
        stored = db.query(LibraryFile).one()
        assert stored.original_name == release
        assert stored.path.endswith("Sorcières Démo 2 (2026)/Sorcières Démo 2.mkv")
        undo_rename(db, renamed.id)
        db.commit()
        stored = db.query(LibraryFile).one()
        assert stored.original_name == release
        assert Path(stored.path).name == release
    finally:
        db.close()


def test_reset_writes_a_backup_and_reseeds_the_default_profile(client, app):
    login(client)
    created = client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Archipel Pilote", "year": 2021},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    refused = client.post("/api/v1/maintenance/reset", json={"confirm": "non"}, headers=csrf(client))
    assert refused.status_code == 422
    done = client.post("/api/v1/maintenance/reset", json={"confirm": "RESET"}, headers=csrf(client))
    assert done.status_code == 200, done.text
    backup = Path(app.state.settings.config_dir) / "backups" / done.json()["backup"]
    assert backup.is_file()
    assert "reset" in backup.name
    db = app.state.session_factory()
    try:
        assert db.query(Media).count() == 0
        assert db.query(MediaItem).count() == 0
        profiles = client.get("/api/v1/quality-profiles")
    finally:
        db.close()
    assert profiles.status_code == 401
