"""Posters, empty roots, manual bind, Lanternes Démo titles, Jellyfin match, hoster fallback."""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from tests.conftest import connect_alldebrid_for_tests, csrf, link_create_json, login

from mediatracker.core.library_plan import (
    LibraryPlanError,
    _suggestion,
    preview_empty_folders,
    remove_empty_folders,
)
from mediatracker.core.pipeline import french_error, tick
from mediatracker.core.selection import parse_hoster_preference
from mediatracker.core.source_select import select_variants, titles_match
from mediatracker.db.models import Download, Link, Media, MediaItem
from mediatracker.integrations.jellyfin import JellyfinClient, from_api_item
from mediatracker.integrations.jellyfin_sync import apply_watches, diagnose_media
from mediatracker.library.scanner import folder_rank
from mediatracker.providers.base import DirectLink, DownloadStatus, Health, SourceResult
from mediatracker.providers.debrid.alldebrid import AllDebridError
from mediatracker.providers.sources.wawacity_html import parse_heading, parse_links
from mediatracker.providers.sources.wawacity_query import apply_listing


class _Hit:
    tmdb_id = 900004
    title = "Forteresse Démo"
    year = 2022
    media_type = "series"
    original_title = "Forteresse Démo"
    poster_url = "https://image.tmdb.org/t/p/w500/dragon.jpg"
    overview = "Dragons " * 40
    season_count = 2
    episode_count = 18
    runtime = None


def test_candidate_payload_uses_a_thumb_and_keeps_the_extra_fields():
    item = _suggestion(_Hit(), "series")
    assert item.poster_url == "https://image.tmdb.org/t/p/w92/dragon.jpg"
    assert item.original_title == "Forteresse Démo"
    assert item.season_count == 2
    assert item.episode_count == 18
    assert item.overview is not None
    assert item.overview.endswith("…")
    assert len(item.overview) <= 180


def test_empty_cleanup_never_proposes_or_deletes_roots(tmp_path: Path):
    films = tmp_path / "Films"
    series = tmp_path / "Séries"
    films.mkdir()
    series.mkdir()
    only_empty = films / "Boite"
    nested = only_empty / "Encore"
    nested.mkdir(parents=True)
    (series / "Reste").mkdir()

    def paths_for(*roots: Path) -> set[str]:
        return {row.path for row in preview_empty_folders(*roots)}

    assert paths_for(films) == {str(only_empty.resolve()), str(nested.resolve())}
    assert str(films.resolve()) not in paths_for(Path(str(films) + "/"))
    link = tmp_path / "films-lien"
    link.symlink_to(films, target_is_directory=True)
    linked = paths_for(link)
    assert str(films.resolve()) not in linked
    assert str(only_empty.resolve()) in linked
    assert str(tmp_path.resolve()) not in paths_for(films, series)
    outside = tmp_path / "dehors"
    outside.mkdir()
    for forbidden in (films, Path(str(films) + "/"), link, films.parent, outside, series):
        with pytest.raises(LibraryPlanError):
            remove_empty_folders([str(forbidden)], films, series)
        assert films.is_dir()
        assert series.is_dir()
        assert outside.is_dir()
    removed = remove_empty_folders([str(only_empty), str(nested)], films)
    assert not only_empty.exists()
    assert str(films.resolve()) not in removed


def test_lanterns_titles_match_hd_vf_pages():
    raw = ["Lanternes Démo - - VF HD", "Lanternes Démo - 4K UHD", "Lanternes Démo - - VF"]
    assert all(titles_match(title, "Lanternes Démo") for title in raw)
    heading = parse_heading("Lanternes Démo - - VF HD")
    quality, language = apply_listing(heading.quality, heading.language, "vf-hq", media_type="series")
    assert heading.title == "Lanternes Démo"
    assert quality == "HD"
    assert language == "VFF"
    pages = []
    for title, page_quality, page_language in (
        ("Lanternes Démo - - VF HD", "HD", "VFF"),
        ("Lanternes Démo - 4K UHD", "4K", "VO"),
        ("Lanternes Démo - - VF", "SD", "VF"),
        ("Autre titre - VF HD", "HD", "VFF"),
    ):
        pages.append(
            SourceResult(
                title=title,
                year=2026,
                media_type="series",
                ref=title,
                url=f"https://example.test/{title}",
                season=1,
                quality=page_quality,
                language=page_language,
            )
        )
    chosen = select_variants(
        pages,
        title="Lanternes Démo",
        year=2026,
        media_type="series",
        seasons={1},
        quality="HD",
        language="VFF",
        allow_fallback=False,
        quality_fallback=("HD",),
        language_fallback=("VFF",),
    )
    assert [page.title for page in chosen] == ["Lanternes Démo - - VF HD"]


def test_uploady_and_dailyuploads_are_parsed_and_1fichier_is_appended_last():
    html = """
    <h1>Séries » Lanternes Démo - Saison 1 [HD] [VF] (2026)</h1>
    <table>
      <tr class="title episode-title"><td colspan="3"><p>Saison 1 Episode 1</p></td></tr>
      <tr class="link-row"><td>Episode 1</td><td>Uploady</td>
        <td><a rel="external nofollow" href="https://dl-protect.link/up1">Télécharger</a></td></tr>
      <tr class="link-row"><td>Episode 1</td><td>DailyUploads</td>
        <td><a rel="external nofollow" href="https://dl-protect.link/du1">Télécharger</a></td></tr>
      <tr class="link-row"><td>Episode 1</td><td>Rapidgator</td>
        <td><a rel="external nofollow" href="https://dl-protect.link/rg1">Télécharger</a></td></tr>
      <tr class="link-row"><td>Episode 1</td><td>1fichier</td>
        <td><a rel="external nofollow" href="https://dl-protect.link/1f1">Télécharger</a></td></tr>
    </table>
    """
    _heading, links = parse_links(html, "https://www.wawacity.example/lanterns")
    by_host = {link.hoster: link for link in links}
    assert set(by_host) >= {"uploady", "dailyuploads", "rapidgator", "1fichier"}
    order = parse_hoster_preference("rapidgator,turbobit,uploady,dailyuploads")
    assert order == ["rapidgator", "turbobit", "uploady", "dailyuploads", "1fichier"]
    moved = parse_hoster_preference("rapidgator,1fichier,turbobit")
    assert moved == ["rapidgator", "turbobit", "1fichier"]
    with pytest.raises(ValueError):
        parse_hoster_preference("rapidgator,uptobox")


def test_folder_rank_prefers_the_tmdb_tag_then_title_and_year():
    media = Media(type="series", title="Lanternes Démo", original_title="Lanternes Démo", year=2026, tmdb_id=42)
    tagged = type("Folder", (), {"title": "Autre", "year": 1999, "tmdb_id": 42})()
    named = type("Folder", (), {"title": "Lanternes Démo", "year": 2026, "tmdb_id": None})()
    loose = type("Folder", (), {"title": "Lanternes Démo S01", "year": 2026, "tmdb_id": None})()
    other = type("Folder", (), {"title": "Détective Démo", "year": 2025, "tmdb_id": 7})()
    assert folder_rank(tagged, media) == 100
    assert folder_rank(named, media) == 80
    assert folder_rank(loose, media) == 60
    assert folder_rank(other, media) == 0


def test_bind_folder_stamps_tmdb_and_does_not_queue(client, app):
    login(client)
    series = Path(app.state.settings.data_dir) / "media" / "Series"
    folder = series / "Suite Ville Démo (2021)"
    folder.mkdir(parents=True)
    (folder / "Suite.Ville.Démo.S03E01.mkv").write_bytes(b"video")
    bound = client.post(
        "/api/v1/library/folders/bind",
        json={"path": str(folder), "tmdb_id": 900002, "type": "series"},
        headers=csrf(client),
    )
    assert bound.status_code == 200, bound.text
    body = bound.json()
    assert body["tmdb_id"] == 900002
    assert body["attached"] == 1
    assert "tmdbid-900002" in body["path"]
    assert client.get("/api/v1/downloads").json()["items"] == []
    listed = client.get(f"/api/v1/media/{body['media_id']}/items").json()
    episode = listed["seasons"][0]["episodes"][0]
    assert episode["downloaded"] is True
    assert episode["season"] == 3
    assert episode["episode"] == 1


def test_follow_attaches_a_folder_with_the_same_title_and_year(client, app):
    login(client)
    series = Path(app.state.settings.data_dir) / "media" / "Series"
    folder = series / "Lanternes Démo (2026)"
    folder.mkdir(parents=True)
    (folder / "Lanternes Démo.S01E01.mkv").write_bytes(b"video")
    created = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Lanternes Démo", "year": 2026, "tmdb_id": 255000},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    assert created.json()["attached_count"] == 0
    from mediatracker.core.attach_deferred import join_pending

    join_pending()
    items = client.get(f"/api/v1/media/{created.json()['id']}/items").json()
    assert items["seasons"][0]["episodes"][0]["downloaded"] is True
    choices = client.get(f"/api/v1/media/{created.json()['id']}/library-folders").json()
    assert choices["items"][0]["score"] >= 80


def test_jellyfin_episode_id_does_not_hide_a_watched_series_episode(client, app):
    login(client)
    created = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Rue Démo", "year": 2025, "tmdb_id": 900003},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    media_id = created.json()["id"]
    added = client.post(
        f"/api/v1/media/{media_id}/items",
        json={"season": 1, "episode": 3, "title": "Trois"},
        headers=csrf(client),
    )
    assert added.status_code == 201, added.text
    raw = {
        "Type": "Episode",
        "Id": "episode-jf",
        "SeriesId": "series-jf",
        "SeriesName": "Rue Démo",
        "ParentIndexNumber": 1,
        "IndexNumber": 3,
        "ProviderIds": {"Tmdb": "999001"},
        "UserData": {"IsPlayed": False, "PlayedPercentage": 95},
    }
    series = {
        "series-jf": {
            "Name": "Rue Démo",
            "ProductionYear": 2025,
            "ProviderIds": {"Tmdb": "900003", "Tvdb": "445566", "Imdb": "tt123"},
        }
    }
    watch = from_api_item(raw, series)
    assert watch is not None
    assert watch.tmdb_id == 999001
    assert watch.series_tmdb_id == 900003
    assert watch.played is True
    assert watch.season == 1 and watch.episode == 3
    db = app.state.session_factory()
    try:
        media = db.get(Media, media_id)
        now = datetime.now(timezone.utc)
        assert apply_watches(db, [watch], None, now) == 1
        db.commit()
        item = db.get(MediaItem, added.json()["id"])
        assert item.watched is True
        manual = MediaItem(
            media_id=media_id,
            season=1,
            episode=4,
            state="missing",
            watched=True,
            watch_source="manual",
        )
        db.add(manual)
        db.commit()
        unplayed = from_api_item(
            {
                **raw,
                "Id": "episode-manual",
                "IndexNumber": 4,
                "UserData": {"Played": False, "PlayedPercentage": 0},
            },
            series,
        )
        assert unplayed is not None and unplayed.played is False
        apply_watches(db, [unplayed], None, now)
        db.commit()
        db.refresh(manual)
        assert manual.watched is True
        assert manual.watch_source == "manual"
        report = diagnose_media(db, media, [watch, unplayed], "2026-10-12T00:00:00+00:00")
        assert report["series_id"] == "series-jf"
        assert report["series_name"] == "Rue Démo"
        matched = next(row for row in report["episodes"] if row["episode"] == 3)
        assert matched["matched"] is True
        assert matched["played"] is True
    finally:
        db.close()
    diag = client.get(f"/api/v1/media/{media_id}/jellyfin")
    assert diag.status_code == 200, diag.text
    assert diag.json()["configured"] is False


def test_jellyfin_pages_until_the_last_short_batch():
    client = JellyfinClient("http://jellyfin.example", "key", "user")
    seen: list[int] = []

    def _request(method: str, path: str, params=None, json=None):
        del method, path, json
        start = int(params["StartIndex"])
        seen.append(start)
        if start == 0:
            return {"Items": [{"Id": f"a{index}"} for index in range(200)], "TotalRecordCount": 201}
        return {"Items": [{"Id": "tail"}], "TotalRecordCount": 201}

    client._request = _request  # type: ignore[method-assign]
    try:
        rows = client.user_items("user")
    finally:
        client.close()
    assert seen == [0, 200]
    assert len(rows) == 201


class _Debrid:
    def __init__(self) -> None:
        self.seen: list[str] = []

    def health(self) -> Health:
        return Health(status="ok", code=None, message="ok", checked_at=datetime.now(timezone.utc))

    def resolve(self, url: str) -> list[str]:
        return [url]

    def unlock(self, url: str) -> DirectLink:
        self.seen.append(url)
        if "rapidgator" in url:
            raise AllDebridError("REDIRECTOR_ERROR", "Could not extract links", retryable=False)
        return DirectLink(url="https://cdn.example/secret.mkv", filename="Episode.mkv", size=32)


class _Downloader:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def add(self, url: str, dest_dir: Path, filename: str | None, headers=None, extra_options=None) -> str:
        del headers, extra_options
        self.urls.append(url)
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / (filename or "video.mkv")).write_bytes(b"x")
        return "g1"

    def status(self, handle: str) -> DownloadStatus:
        del handle
        return DownloadStatus(state="downloading", done_bytes=1, total_bytes=8, speed=1)

    def remove(self, handle: str, delete_files: bool = False) -> None:
        del handle, delete_files


def _enable(app) -> None:
    connect_alldebrid_for_tests(app)


def test_french_errors_are_not_repeated():
    assert french_error("LINK_DOWN", "hors ligne") == "Lien hors ligne."
    assert french_error("REDIRECTOR_ERROR", "Could not extract links") == (
        "Le redirecteur n'a pas pu extraire les liens."
    )
    assert french_error("LINK_DOWN", "hors ligne hors ligne") == "Lien hors ligne."
    repeated = french_error("REDIRECTOR_ERROR", "Could not extract links Could not extract links")
    assert "Could not extract links" not in repeated


def test_redirector_error_tries_the_next_hoster_in_one_tick(client, app):
    login(client)
    _enable(app)
    media = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Suite Ville Démo", "year": 2021, "tmdb_id": 900002},
        headers=csrf(client),
    )
    assert media.status_code == 201, media.text
    media_id = media.json()["id"]
    first = client.post(
        "/api/v1/links",
        json=link_create_json(
            media_id,
            "https://rapidgator.net/file/dead",
            season=3,
            episode=1,
            start=True,
        ),
        headers=csrf(client),
    )
    assert first.status_code == 201, first.text
    second = client.post(
        "/api/v1/links",
        json=link_create_json(
            media_id,
            "https://uploady.io/file/next",
            season=3,
            episode=1,
            start=False,
        ),
        headers=csrf(client),
    )
    assert second.status_code == 201, second.text
    debrid = _Debrid()
    now = datetime(2026, 10, 12, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=now, debrid=debrid, downloader=_Downloader())
        row = db.get(Download, first.json()["download_id"])
        assert row.state == "queued"
        assert row.error is None
        buried = db.query(Link).filter(Link.url.contains("rapidgator")).one()
        assert buried.dead is True
        assert buried.last_error == "Le redirecteur n'a pas pu extraire les liens."
    finally:
        db.close()
    assert debrid.seen == ["https://rapidgator.net/file/dead", "https://uploady.io/file/next"]


def test_manual_1fichier_link_is_queued(client, app):
    login(client)
    _enable(app)
    media = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Détective Démo", "year": 2025},
        headers=csrf(client),
    )
    assert media.status_code == 201, media.text
    created = client.post(
        "/api/v1/links",
        json=link_create_json(
            media.json()["id"],
            "https://1fichier.com/file/abc",
            season=1,
            episode=1,
            start=True,
        ),
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    debrid = _Debrid()
    now = datetime(2026, 10, 12, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=now, debrid=debrid, downloader=_Downloader())
        row = db.get(Download, created.json()["download_id"])
        assert row.state == "queued"
        assert debrid.seen == ["https://1fichier.com/file/abc"]
        listed = client.get("/api/v1/downloads").json()
        assert listed["items"][0]["hoster"] == "1fichier"
    finally:
        db.close()


def test_dead_links_fail_instead_of_staying_pending_and_retry_all_rediscovers(client, app):
    login(client)
    _enable(app)
    media = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Détective Démo", "year": 2025},
        headers=csrf(client),
    )
    assert media.status_code == 201, media.text
    created = client.post(
        "/api/v1/links",
        json={
            "media_id": media.json()["id"],
            "url": "https://rapidgator.net/file/dead-end",
            "season": 1,
            "episode": 1,
            "start": True,
        },
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    debrid = _Debrid()
    debrid.fail_url = "https://rapidgator.net/file/dead-end"

    def unlock(url: str) -> DirectLink:
        debrid.seen.append(url)
        if url == debrid.fail_url:
            raise AllDebridError("LINK_DOWN", "hors ligne", retryable=False)
        return DirectLink(url="https://cdn.example/secret.mkv", filename="Episode.mkv", size=32)

    debrid.unlock = unlock  # type: ignore[method-assign]
    now = datetime(2026, 10, 12, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=now, debrid=debrid, downloader=_Downloader())
        row = db.get(Download, created.json()["download_id"])
        assert row.state == "failed"
        assert row.state != "pending"
        listed = client.get("/api/v1/downloads").json()
        assert listed["items"][0]["state"] == "failed"
        cleared = client.post("/api/v1/downloads/clear-completed", headers=csrf(client))
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["removed"] == 0
        again = client.get("/api/v1/downloads").json()
        assert again["items"][0]["state"] == "failed"
        retried = client.post(f"/api/v1/downloads/{row.id}/retry-all", headers=csrf(client))
        assert retried.status_code == 200, retried.text
        assert retried.json()["state"] == "pending"
        assert retried.json()["error_code"] == "REDISCOVER"
        db.refresh(row)
        row.state = "failed"
        second = Download(
            media_item_id=row.media_item_id,
            state="failed",
            error_code="LINK_DOWN",
            error="Lien hors ligne.",
            created_at=now,
        )
        db.add(second)
        db.commit()
        bulk = client.post("/api/v1/downloads/retry-failed", headers=csrf(client))
        assert bulk.status_code == 200, bulk.text
        assert bulk.json()["scheduled"] == 2
        db.refresh(row)
        db.refresh(second)
        assert row.next_retry_at != second.next_retry_at
    finally:
        db.close()
