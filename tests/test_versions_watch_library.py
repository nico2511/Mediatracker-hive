"""Version listings, manual watch, and empty-folder cleanup."""

from datetime import date, datetime, timezone
from pathlib import Path

from tests.conftest import csrf, login

from mediatracker.core.library_plan import LibraryPlanError, preview_empty_folders, remove_empty_folders
from mediatracker.core.source_poll import expected_slots
from mediatracker.db.models import Media, MediaItem
from mediatracker.integrations.jellyfin import JellyfinWatch
from mediatracker.integrations.jellyfin_sync import apply_played
from mediatracker.providers.base import SourcePreferences
from mediatracker.providers.sources.wawacity_html import parse_heading, parse_search
from mediatracker.providers.sources.wawacity_query import search_slugs, search_url

_FIXTURES = Path(__file__).parent / "fixtures" / "wawacity"


def test_series_and_film_listings_follow_the_defaults():
    series = search_slugs("series", SourcePreferences(quality="HD", language="VFF"))
    films = search_slugs("film", SourcePreferences(quality="HD", language="VFF"))
    assert series == ["vf-hq"]
    assert films[0] == "blu-ray_1080p-720p"
    assert "dvdsrc-r5-ts-cam" not in films
    assert "film-vostfr" not in films
    assert "_film-vo" not in films
    optional = search_slugs(
        "film",
        SourcePreferences(
            quality="HD",
            language="VFF",
            allow_fallback=True,
            quality_fallback=["4K", "SD"],
            language_fallback=["VOSTFR", "VO"],
        ),
    )
    assert "ultra-hd-4k" in optional
    assert "film-vostfr" in optional
    assert "dvdsrc-r5-ts-cam" not in optional
    url = search_url("https://www.wawacity.ing", "series", "Hôpital Démo", "vf-hq")
    assert url.endswith("?p=series&search=H%C3%B4pital+D%C3%A9mo&s=vf-hq")


def test_version_labels_and_toy_story_fixture():
    heading = parse_heading("[Séries] - Saison 1- VF HD")
    assert heading.season == 1
    assert heading.quality == "HD"
    html = (_FIXTURES / "search_versions.html").read_text(encoding="utf-8")
    hits = parse_search(html, "https://www.wawacity.ing")
    toy = next(hit for hit in hits if "59834-jouet-demo-5" in hit.url and "cam" not in hit.url)
    cam = next(hit for hit in hits if hit.url.endswith("-cam"))
    hospital_hit = next(hit for hit in hits if "hopital-demo" in hit.url)
    assert toy.quality == "1080p"
    assert toy.language == "MULTI"
    assert cam.rejection == "Qualité caméra ou TS exclue."
    assert hospital_hit.season == 1
    assert hospital_hit.quality == "HD"


def test_manual_watch_survives_jellyfin_and_is_not_downloaded(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        media = Media(type="series", title="Hôpital Démo", year=2025, tmdb_id=1, created_at=datetime.now(timezone.utc))
        db.add(media)
        db.flush()
        item = MediaItem(
            media_id=media.id,
            season=1,
            episode=1,
            air_date=date(2026, 1, 1),
            state="missing",
            watched=True,
            watch_source="manual",
        )
        db.add(item)
        db.commit()
        now = datetime.now(timezone.utc)
        apply_played(
            db,
            item,
            JellyfinWatch(
                item_id="jf",
                item_type="Episode",
                tmdb_id=1,
                season=1,
                episode=1,
                path=None,
                played=False,
            ),
            now,
        )
        assert item.watched is True
        assert item.watch_source == "manual"
        slots = expected_slots(media, [item], today=date(2026, 10, 11), window_days=30, force=True)
        assert slots == []
    finally:
        db.close()


def test_empty_folder_preview_keeps_media_and_roots(tmp_path: Path):
    films = tmp_path / "Films"
    empty = films / "Vide"
    junk = films / "Restes"
    kept = films / "Archipel Pilote (2021)"
    tracker = films / ".mediatracker" / "cache"
    empty.mkdir(parents=True)
    junk.mkdir()
    (junk / "Thumbs.db").write_bytes(b"x")
    (junk / "movie.nfo").write_text("nfo", encoding="utf-8")
    kept.mkdir()
    (kept / "Archipel Pilote (2021).mkv").write_bytes(b"v")
    (kept / "Archipel Pilote (2021).srt").write_text("sub", encoding="utf-8")
    tracker.mkdir(parents=True)
    (tracker / "note.txt").write_text("keep", encoding="utf-8")
    found = [item.path for item in preview_empty_folders(films)]
    assert str(empty) in found
    assert str(junk) in found
    assert str(kept) not in found
    assert str(films) not in found
    assert all(".mediatracker" not in path for path in found)
    removed = remove_empty_folders(found, films)
    assert str(empty.resolve()) in removed
    assert not empty.exists()
    assert kept.is_dir()
    assert (tracker / "note.txt").is_file()


def test_empty_folder_never_proposes_syncthing_or_hidden_markers(tmp_path: Path):
    films = tmp_path / "Films"
    series = tmp_path / "Séries"
    films.mkdir()
    series.mkdir()
    markers = [
        films / ".stfolder",
        series / ".stfolder",
        films / ".stversions",
        films / ".Trash-1000",
        films / "@eaDir",
        films / "lost+found",
        films / "#recycle",
        films / ".cache",
        series / ".mediatracker",
    ]
    for folder in markers:
        folder.mkdir()
        (folder / "keep").write_text("marker", encoding="utf-8")
    ignore = films / "AvecRegle"
    ignore.mkdir()
    (ignore / ".stignore").write_text("(?d).stfolder", encoding="utf-8")
    nested = films / "Boite"
    (nested / ".stfolder").mkdir(parents=True)
    empty = films / "VraimentVide"
    empty.mkdir()
    junk = series / "Restes"
    junk.mkdir()
    (junk / "poster.jpg").write_bytes(b"ab")
    (junk / "poster.jpg").write_bytes(b"abcd")
    (junk / "extra.jpg").write_bytes(b"xy")
    (junk / "show.nfo").write_text("nfo", encoding="utf-8")
    rows = preview_empty_folders(films, series)
    paths = {row.path for row in rows}
    assert str(empty) in paths
    assert str(junk) in paths
    assert str(ignore) not in paths
    assert str(nested) not in paths
    assert all(".stfolder" not in path for path in paths)
    assert all(".stversions" not in path for path in paths)
    assert all(".Trash" not in path for path in paths)
    assert all("@eaDir" not in path for path in paths)
    assert all("lost+found" not in path for path in paths)
    assert all("#recycle" not in path for path in paths)
    assert all(".mediatracker" not in path for path in paths)
    assert all("/.cache" not in path and not path.endswith(".cache") for path in paths)
    described = next(row for row in rows if row.path == str(junk))
    assert described.file_count == 3
    assert described.size == 4 + 2 + 3
    assert described.summary == "Effectivement vide · 3 fichiers · .jpg ×2, .nfo ×1"
    for folder in markers:
        try:
            remove_empty_folders([str(folder)], films, series)
        except LibraryPlanError as exc:
            assert "marqueur" in exc.message
        else:
            raise AssertionError(folder.name)
        assert folder.is_dir()
    try:
        remove_empty_folders([str(ignore), str(empty)], films, series)
    except LibraryPlanError:
        pass
    else:
        raise AssertionError("ignore file")
    assert ignore.is_dir()
    assert empty.is_dir()
    removed = remove_empty_folders([str(empty), str(junk)], films, series)
    assert str(empty) in removed
    assert str(junk) in removed
    assert not empty.exists()
    assert (films / ".stfolder").is_dir()


def test_watch_endpoint_marks_a_season(client):
    login(client)
    created = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Hôpital Démo", "year": 2025},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    media_id = created.json()["id"]
    episode = client.post(
        f"/api/v1/media/{media_id}/items",
        json={"season": 1, "episode": 2, "title": "Deux"},
        headers=csrf(client),
    )
    assert episode.status_code == 201, episode.text
    marked = client.post(
        f"/api/v1/media/{media_id}/watch",
        json={"season": 1},
        headers=csrf(client),
    )
    assert marked.status_code == 200, marked.text
    assert marked.json()["updated"] == 1
    again = client.get(f"/api/v1/media/{media_id}/items")
    row = again.json()["seasons"][0]["episodes"][0]
    assert row["watched"] is True
    assert row["watch_source"] == "manual"
