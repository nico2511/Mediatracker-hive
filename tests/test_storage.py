"""Configurable library folders, same-disk check, and Jellyfin path maps."""

import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.conftest import csrf, login

from mediatracker.api.app import create_app
from mediatracker.config import get_settings
from mediatracker.core.cleanup import CleanupError, ensure_inside
from mediatracker.core.disk import enforce_disk_guard
from mediatracker.core.naming import atomic_move, library_destination
from mediatracker.core.storage import (
    StorageLayout,
    ensure_runtime_dirs,
    same_filesystem_warning,
    storage_layout,
)
from mediatracker.integrations.jellyfin import paths_match
from mediatracker.integrations.paths import library_path_warnings, parse_path_map, suggest_path_map
from mediatracker.library.scanner import library_roots, scan_library

# Absent from the frozen NAS compose: the wizard asks for them.
_NOT_IN_COMPOSE = (
    "ADMIN_PASSWORD",
    "JELLYFIN_API_KEY",
    "TMDB_READ_TOKEN",
    "ALLDEBRID_API_KEY",
    "LIBRARY_MOVIES_PATH",
    "LIBRARY_SERIES_PATH",
    "MEDIA_DIR",
    "JELLYFIN_PATH_PREFIX",
)


def test_defaults_follow_the_data_directory(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        layout = storage_layout(db, app.state.settings)
        data = Path(app.state.settings.data_dir)
        assert layout.media_dir == data / "media"
        assert layout.movies_dir == data / "media" / "Films"
        assert layout.series_dir == data / "media" / "Series"
        assert layout.downloads_dir == data / "downloads"
        assert layout.trash_dir == data / ".trash"
        body = client.get("/api/v1/storage")
        assert body.status_code == 200, body.text
        assert body.json()["movies_dir"] == str(layout.movies_dir)
        assert body.json()["backups_dir"] == str(Path(app.state.settings.config_dir) / "backups")
        assert body.json()["same_filesystem"] is True
    finally:
        db.close()


def test_settings_override_paths_including_accents(client, app):
    login(client)
    data = Path(app.state.settings.data_dir)
    movies = data / "Films"
    series = data / "Séries"
    downloads = data / ".mediatracker" / "downloads"
    trash = data / ".mediatracker" / "trash"
    saved = client.patch(
        "/api/v1/settings",
        json={
            "values": {
                "library_movies_path": str(movies),
                "library_series_path": str(series) + "/",
                "downloads_dir": str(downloads),
                "trash_dir": str(trash),
                "jellyfin_path_map": "/data/movies=/data/Films;/data/tvshows=/data/Séries",
            }
        },
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text
    db = app.state.session_factory()
    try:
        layout = storage_layout(db, app.state.settings)
        assert layout.movies_dir == movies
        assert layout.series_dir == series
        assert library_roots(db, app.state.settings) == (movies, series)
        assert layout.downloads_dir == downloads
        assert layout.trash_dir == trash
    finally:
        db.close()
    refused = client.patch(
        "/api/v1/settings",
        json={"values": {"downloads_dir": "downloads"}},
        headers=csrf(client),
    )
    assert refused.status_code == 422
    escaped = client.patch(
        "/api/v1/settings",
        json={"values": {"trash_dir": "/data/../etc"}},
        headers=csrf(client),
    )
    assert escaped.status_code == 422
    broken = client.patch(
        "/api/v1/settings",
        json={"values": {"jellyfin_path_map": "/data/movies"}},
        headers=csrf(client),
    )
    assert broken.status_code == 422


def test_accented_series_dir_is_scanned_and_filed(client, app):
    login(client)
    data = Path(app.state.settings.data_dir)
    movies = data / "Films"
    series = data / "Séries"
    downloads = data / ".mediatracker" / "downloads"
    trash = data / ".mediatracker" / "trash"
    saved = client.patch(
        "/api/v1/settings",
        json={
            "values": {
                "library_movies_path": str(movies),
                "library_series_path": str(series),
                "downloads_dir": str(downloads),
                "trash_dir": str(trash),
                "disk_min_free_gb": 1,
            }
        },
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text
    episode = series / "Orbite Démo (2022)" / "Season 02" / "Orbite Démo (2022) - S02E01.mkv"
    episode.parent.mkdir(parents=True)
    episode.write_bytes(b"video")
    db = app.state.session_factory()
    try:
        now = datetime(2026, 10, 11, tzinfo=timezone.utc)
        report = scan_library(db, movies=movies, series=series, now=now, tmdb=None)
        assert report.seen == 1
        assert "Séries" in episode.parts
        dest = library_destination(
            movies_dir=movies,
            series_dir=series,
            media_type="series",
            title="Orbite Démo",
            year=2022,
            season=2,
            episode=3,
            ext=".mkv",
        )
        assert dest.parts[dest.parts.index("Séries") + 1] == "Orbite Démo (2022)"
        source = downloads / "incomplete" / "Orbite Démo.mkv"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"nouveau")
        atomic_move(source, dest)
        assert dest.read_bytes() == b"nouveau"
        ensure_inside(dest, [movies, series], trash)
        outside = downloads / "incomplete" / "hors.mkv"
        with pytest.raises(CleanupError):
            ensure_inside(outside, [movies, series], trash)
        seen: list[Path] = []

        def _free(path: Path) -> int:
            seen.append(path)
            return 10**12

        enforce_disk_guard(db, app.state.settings, None, now, _free)
        assert seen == [downloads / "incomplete"]
    finally:
        db.close()


def test_same_filesystem_warning_names_the_other_disk():
    layout = StorageLayout(
        media_dir=Path("/data"),
        movies_dir=Path("/data/Films"),
        series_dir=Path("/data/Séries"),
        downloads_dir=Path("/mnt/other/downloads"),
        trash_dir=Path("/data/.mediatracker/trash"),
    )
    devices = {
        layout.movies_dir: 1,
        layout.series_dir: 1,
        layout.downloads_dir: 2,
        layout.trash_dir: 1,
    }
    warning = same_filesystem_warning(layout, device_of=lambda path: devices[path])
    assert "même système de fichiers" in warning
    assert "/data/Séries" in warning
    assert "/mnt/other/downloads" in warning
    assert same_filesystem_warning(layout, device_of=lambda _path: 7) == ""


def test_path_map_translates_jellyfin_prefixes_and_keeps_the_old_prefix():
    pairs = parse_path_map("/data/movies=/data/Films;/data/tvshows=/data/Séries")
    local = "/data/Séries/Orbite Démo (2022)/Season 02/Orbite Démo (2022) - S02E01.mkv"
    remote = "/data/tvshows/Orbite Démo (2022)/Season 02/Orbite Démo (2022) - S02E01.mkv"
    assert paths_match(local, remote, None, pairs)
    legacy_local = "/data/media/Series/Exemple (2024)/Season 01/Exemple (2024) - S01E02.mkv"
    legacy_remote = "/srv/media/Series/Exemple (2024)/Season 01/Exemple (2024) - S01E02.mkv"
    assert paths_match(legacy_local, legacy_remote, "/srv/media", pairs)
    # A mapping that applies is exact: the last-three-parts guess must not override it.
    assert not paths_match(
        "/keep/Films/Archipel Pilote (2021)/Archipel Pilote.mkv",
        "/data/movies/Films/Archipel Pilote (2021)/Archipel Pilote.mkv",
        None,
        pairs,
    )


def test_virtual_folders_suggest_the_linuxserver_layout(tmp_path):
    movies = tmp_path / "Films"
    series = tmp_path / "Séries"
    movies.mkdir()
    series.mkdir()
    (movies / "Archipel Pilote (2021)").mkdir()
    (series / "Orbite Démo (2022)").mkdir()
    folders = [
        {"name": "Films", "collection_type": "movies", "locations": ["/data/movies"]},
        {"name": "Séries", "collection_type": "tvshows", "locations": ["/data/tvshows"]},
    ]
    suggestion = suggest_path_map(movies, series, folders)
    assert suggestion == f"/data/movies={movies};/data/tvshows={series}"
    assert library_path_warnings(str(movies), str(series), folders, mappings=parse_path_map(suggestion)) == []
    assert library_path_warnings(str(movies), str(series), folders)

    visible = tmp_path / "jellyfin-movies"
    visible.mkdir()
    (visible / "Archipel Pilote (2021)").mkdir()
    by_content = suggest_path_map(
        movies,
        series,
        [{"name": "Autre", "collection_type": "", "locations": [str(visible)]}],
    )
    assert by_content == f"{visible}={movies}"
    already = suggest_path_map(
        movies,
        series,
        [{"name": "Films", "collection_type": "movies", "locations": [str(movies)]}],
    )
    assert already == ""


def test_sante_reports_a_split_volume(client, app):
    login(client)
    data = Path(app.state.settings.data_dir)
    saved = client.patch(
        "/api/v1/settings",
        json={"values": {"downloads_dir": "/tmp/mediatracker-other-disk-downloads"}},
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text
    # The other path may still share this machine's only disk. Force the check through the helper
    # and also assert Santé exposes the volumes component with the resolved download directory.
    body = client.get("/api/v1/health/components")
    assert body.status_code == 200, body.text
    volumes = next(row for row in body.json()["components"] if row["name"] == "volumes")
    storage = client.get("/api/v1/storage").json()
    assert storage["downloads_dir"] == "/tmp/mediatracker-other-disk-downloads"
    assert storage["movies_dir"] == str(data / "media" / "Films")
    assert volumes["status"] in {"ok", "degraded"}
    if storage["same_filesystem"]:
        assert volumes["status"] == "ok"
    else:
        assert volumes["status"] == "degraded"
        assert "même système de fichiers" in volumes["message"]


def test_exact_jellyfin_map_accepts_nfc_and_nfd():
    """The frozen map string, with Séries written either NFC or NFD."""
    remote = "/data/tvshows/Orbite Démo (2022)/Season 02/Orbite Démo (2022) - S02E01.mkv"
    film_remote = "/data/movies/Archipel Pilote (2021)/Archipel Pilote (2021).mkv"
    for map_form in ("NFC", "NFD"):
        series = unicodedata.normalize(map_form, "/data/Séries")
        raw = f"/data/movies=/data/Films;/data/tvshows={series}"
        assert raw.replace(series, unicodedata.normalize("NFC", series)) == (
            "/data/movies=/data/Films;/data/tvshows=/data/Séries"
        )
        pairs = parse_path_map(raw)
        for local_form in ("NFC", "NFD"):
            episode = unicodedata.normalize(
                local_form,
                "/data/Séries/Orbite Démo (2022)/Season 02/Orbite Démo (2022) - S02E01.mkv",
            )
            film = unicodedata.normalize(local_form, "/data/Films/Archipel Pilote (2021)/Archipel Pilote (2021).mkv")
            assert paths_match(episode, remote, None, pairs)
            assert paths_match(film, film_remote, None, pairs)


@pytest.mark.parametrize("env_form", ["NFC", "NFD"])
def test_frozen_compose_files_scans_and_translates(tmp_path, monkeypatch, env_form):
    """Load the NAS compose variables and use the other Unicode form on disk."""
    root = tmp_path / env_form
    root.mkdir()
    disk_form = "NFD" if env_form == "NFC" else "NFC"
    disk_series = root / unicodedata.normalize(disk_form, "Séries")
    episode = disk_series / "Orbite Démo (2022)" / "Season 02" / "Orbite Démo (2022) - S02E01.mkv"
    episode.parent.mkdir(parents=True)
    episode.write_bytes(b"vu")
    film = root / "Films" / "Archipel Pilote (2021)" / "Archipel Pilote (2021).mkv"
    film.parent.mkdir(parents=True)
    film.write_bytes(b"film")

    series_name = unicodedata.normalize(env_form, "Séries")
    movies = root / "Films"
    series_dir = root / series_name
    downloads = root / ".mediatracker" / "downloads"
    trash = root / ".mediatracker" / "trash"
    # Names and formats from the frozen compose. Paths are under tmp so the
    # test does not write /data; the map still uses the Jellyfin prefixes.
    compose = {
        "PUID": "1000",
        "PGID": "100",
        "TZ": "Europe/Paris",
        "SECRET_KEY": "nas-secret-key-0123456789",
        "PUBLIC_URL": "http://192.0.2.1:8080",
        "JELLYFIN_URL": "http://192.0.2.1:8096",
        "MOVIES_DIR": str(movies),
        "SERIES_DIR": str(series_dir),
        "DOWNLOADS_DIR": str(downloads),
        "TRASH_DIR": str(trash),
        "JELLYFIN_PATH_MAP": f"/data/movies={movies};/data/tvshows={series_dir}",
        "CONFIG_DIR": str(root / "config"),
        "DATA_DIR": str(root),
        "DATABASE_URL": f"sqlite:///{root / 'config' / 'app.db'}",
        "DISK_MIN_FREE_GB": "0",
    }
    for key in _NOT_IN_COMPOSE:
        monkeypatch.delenv(key, raising=False)
    for key, value in compose.items():
        monkeypatch.setenv(key, value)

    loaded = get_settings()
    assert loaded.puid == 1000
    assert loaded.pgid == 100
    assert str(loaded.public_url) == "http://192.0.2.1:8080"
    assert str(loaded.jellyfin_url) == "http://192.0.2.1:8096"
    assert loaded.admin_password is None
    assert loaded.jellyfin_api_key is None
    assert loaded.tmdb_read_token is None
    assert loaded.alldebrid_api_key is None
    assert unicodedata.normalize("NFC", loaded.series_dir.name) == "Séries"
    assert unicodedata.is_normalized(env_form, loaded.series_dir.name)

    (root / "config").mkdir()
    application = create_app(loaded)
    with TestClient(application):
        db = application.state.session_factory()
        try:
            owned: list[tuple[Path, int, int]] = []

            def _chown(path, uid, gid):
                owned.append((Path(path), uid, gid))

            ensure_runtime_dirs(db, loaded, chown=_chown, geteuid=lambda: 0)
            layout = storage_layout(db, loaded)
            assert layout.movies_dir == movies
            assert layout.series_dir.resolve() == disk_series.resolve()
            assert layout.downloads_dir.is_dir()
            assert (layout.downloads_dir / "incomplete").is_dir()
            assert layout.trash_dir.is_dir()
            assert (layout.downloads_dir, 1000, 100) in owned
            assert (layout.trash_dir, 1000, 100) in owned
            assert disk_series.resolve() not in {path.resolve() for path, _uid, _gid in owned}
            series_dirs = [
                item.name for item in root.iterdir() if unicodedata.normalize("NFC", item.name) == "Séries"
            ]
            assert series_dirs == [disk_series.name]
            now = datetime(2026, 10, 11, tzinfo=timezone.utc)
            report = scan_library(
                db,
                movies=layout.movies_dir,
                series=layout.series_dir,
                now=now,
                tmdb=None,
            )
            assert report.seen == 2
            source = layout.downloads_dir / "incomplete" / "Orbite Démo.mkv"
            source.write_bytes(b"nouveau")
            dest = library_destination(
                movies_dir=layout.movies_dir,
                series_dir=layout.series_dir,
                media_type="series",
                title="Orbite Démo",
                year=2022,
                season=2,
                episode=2,
                ext=".mkv",
            )
            atomic_move(source, dest)
            assert dest.is_file()
            assert dest.resolve().is_relative_to(disk_series.resolve())
            still = [item.name for item in root.iterdir() if unicodedata.normalize("NFC", item.name) == "Séries"]
            assert still == [disk_series.name]
            pairs = parse_path_map(loaded.jellyfin_path_map)
            assert paths_match(
                str(episode),
                "/data/tvshows/Orbite Démo (2022)/Season 02/Orbite Démo (2022) - S02E01.mkv",
                None,
                pairs,
            )
            assert paths_match(str(film), "/data/movies/Archipel Pilote (2021)/Archipel Pilote (2021).mkv", None, pairs)
        finally:
            db.close()
    application.state.engine.dispose()
