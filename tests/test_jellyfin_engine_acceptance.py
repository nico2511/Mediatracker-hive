"""Tests ciblés du moteur d'identification (spec import Jellyfin)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from mediatracker.integrations.jellyfin_db_export import export_history_bundle
from mediatracker.integrations.jellyfin_history_engine import associate_logs, build, finalize, load

USER = "11111111-1111-1111-1111-111111111111"
PH = "00000000000000000000000000000001"
TYPE_MOVIE = "MediaBrowser.Controller.Entities.Movies.Movie"
TYPE_SERIES = "MediaBrowser.Controller.Entities.TV.Series"
TYPE_EP = "MediaBrowser.Controller.Entities.TV.Episode"

_UD_INSERT = (
    "INSERT INTO UserData (ItemId, UserId, CustomDataKey, Played, PlayCount, LastPlayedDate) "
    "VALUES ('{item_id}', '{user}', '{custom_key}', 1, 1, '{played_at}');"
)


def _userdata_sql(item_id: str, custom_key: str, played_at: str, *, user: str = USER) -> str:
    return _UD_INSERT.format(item_id=item_id, user=user, custom_key=custom_key, played_at=played_at)


def _insert_series_row(series_id: str, title: str, year: int) -> str:
    return (
        f"INSERT INTO BaseItems VALUES ('{series_id}', '{TYPE_SERIES}', '{title}', {year}, "
        "NULL, NULL, NULL, NULL, 0, NULL);"
    )


def _efcore_db(path: Path, *, userdata: str = "", activity: str = "", items: str = "", providers: str = "") -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        f"""
        CREATE TABLE Users (Id TEXT PRIMARY KEY, Username TEXT NOT NULL);
        INSERT INTO Users VALUES ('{USER}', 'demo');
        CREATE TABLE BaseItems (
            Id TEXT PRIMARY KEY, Type TEXT, Name TEXT, ProductionYear INTEGER,
            SeriesName TEXT, ParentIndexNumber INTEGER, IndexNumber INTEGER,
            SeriesId TEXT, IsMovie INTEGER, RunTimeTicks INTEGER
        );
        {items}
        CREATE TABLE BaseItemProviders (ItemId TEXT, ProviderId TEXT, ProviderValue TEXT,
            PRIMARY KEY (ItemId, ProviderId));
        {providers}
        CREATE TABLE UserData (
            ItemId TEXT, UserId TEXT, CustomDataKey TEXT, Played INTEGER, PlayCount INTEGER,
            LastPlayedDate TEXT, PlaybackPositionTicks INTEGER, RetentionDate TEXT,
            PRIMARY KEY (ItemId, UserId, CustomDataKey)
        );
        {userdata}
        CREATE TABLE ActivityLogs (
            Id INTEGER PRIMARY KEY AUTOINCREMENT, Type TEXT, Name TEXT, UserId TEXT, ItemId TEXT, DateCreated TEXT
        );
        {activity}
        """
    )
    conn.commit()
    conn.close()


def test_same_season_episode_two_series_no_cross_titles(tmp_path):
    s1, s2 = "aaaaaaaa-1111-1111-1111-111111111111", "bbbbbbbb-2222-2222-2222-222222222222"
    e1, e2 = "cccccccc-3333-3333-3333-333333333333", "dddddddd-4444-4444-4444-444444444444"
    ts = "2024-06-01T10:00:00.0000000Z"
    db = tmp_path / "j.db"
    _efcore_db(
        db,
        items=f"""
        INSERT INTO BaseItems VALUES ('{s1}', '{TYPE_SERIES}', 'Serie Alpha', 2020, NULL, NULL, NULL, NULL, 0, NULL);
        INSERT INTO BaseItems VALUES ('{s2}', '{TYPE_SERIES}', 'Serie Beta', 2021, NULL, NULL, NULL, NULL, 0, NULL);
        INSERT INTO BaseItems VALUES ('{e1}', '{TYPE_EP}', 'Ep Alpha', 2020, 'Serie Alpha', 1, 3, '{s1}', 0, NULL);
        INSERT INTO BaseItems VALUES ('{e2}', '{TYPE_EP}', 'Ep Beta', 2021, 'Serie Beta', 1, 3, '{s2}', 0, NULL);
        """,
        providers=f"""
        INSERT INTO BaseItemProviders VALUES ('{s1}', 'Tvdb', '990101');
        INSERT INTO BaseItemProviders VALUES ('{s2}', 'Tvdb', '990202');
        """,
        userdata="\n".join(
            [
                _userdata_sql(e1, e1.lower(), ts),
                _userdata_sql(e2, e2.lower(), ts),
            ]
        ),
    )
    bundle = export_history_bundle(jellyfin_db=db, user_id=USER)
    eps = [r for r in bundle.rows if r.type == "episode" and r.season == 1 and r.episode == 3]
    assert len(eps) == 2
    titles = {r.series_title for r in eps}
    assert titles == {"Serie Alpha", "Serie Beta"}


def test_series_guid_only_episode_is_sure_with_library(tmp_path):
    sguid = "eeeeeeee-5555-5555-5555-555555555555"
    db = tmp_path / "j.db"
    _efcore_db(
        db,
        items=_insert_series_row(sguid, "Serie Guid", 2022),
        userdata=f"""
        INSERT INTO UserData (ItemId, UserId, CustomDataKey, Played, PlayCount, LastPlayedDate)
        VALUES ('{PH}', '{USER}', '{sguid}001002', 1, 1, '2024-01-01T12:00:00.0000000Z');
        """,
    )
    bundle = export_history_bundle(jellyfin_db=db, user_id=USER)
    assert len(bundle.rows) == 1
    row = bundle.rows[0]
    assert row.type == "episode" and row.season == 1 and row.episode == 2
    assert row.confiance in {"sûr", "probable"}


def test_shared_imdb_two_tvdb_marks_probable(tmp_path):
    """Même imdb de série rattaché à deux tvdb (bibliothèque) → stat + drapeau probable."""
    s1 = "aaaaaaaa-1111-1111-1111-111111111111"
    s2 = "bbbbbbbb-2222-2222-2222-222222222222"
    ts = "2024-02-01T10:00:00.0000000Z"
    db = tmp_path / "j.db"
    _efcore_db(
        db,
        items="\n".join(
            [
                _insert_series_row(s1, "Serie Partagee A", 2020),
                _insert_series_row(s2, "Serie Partagee B", 2021),
            ]
        ),
        providers=f"""
        INSERT INTO BaseItemProviders VALUES ('{s1}', 'Tvdb', '990001');
        INSERT INTO BaseItemProviders VALUES ('{s1}', 'Imdb', 'tt9990001');
        INSERT INTO BaseItemProviders VALUES ('{s2}', 'Tvdb', '990002');
        INSERT INTO BaseItemProviders VALUES ('{s2}', 'Imdb', 'tt9990001');
        """,
        userdata="\n".join(
            [
                _userdata_sql(PH, "990001001001", ts),
                _userdata_sql(PH, "tt9990001001001", ts),
            ]
        ),
    )
    bundle = export_history_bundle(jellyfin_db=db, user_id=USER)
    assert bundle.stats is not None
    assert bundle.stats.summary.get("imdb_serie_partage_plusieurs_tvdb", 0) >= 1
    assert any("imdb de série partagé" in (r.drapeaux or "") for r in bundle.rows)


def test_homonym_series_imdb_only_probable(tmp_path):
    s1 = "aaaaaaaa-1111-1111-1111-111111111111"
    s2 = "bbbbbbbb-2222-2222-2222-222222222222"
    e1 = "cccccccc-3333-3333-3333-333333333333"
    db = tmp_path / "j.db"
    _efcore_db(
        db,
        items=f"""
        INSERT INTO BaseItems VALUES ('{s1}', '{TYPE_SERIES}', 'Serie Homonyme', 2020, NULL, NULL, NULL, NULL, 0, NULL);
        INSERT INTO BaseItems VALUES ('{s2}', '{TYPE_SERIES}', 'Serie Homonyme', 2021, NULL, NULL, NULL, NULL, 0, NULL);
        INSERT INTO BaseItems VALUES ('{e1}', '{TYPE_EP}', 'Pilot', 2020, 'Serie Homonyme', 1, 1, '{s1}', 0, NULL);
        """,
        providers=f"""
        INSERT INTO BaseItemProviders VALUES ('{s1}', 'Tvdb', '990301');
        INSERT INTO BaseItemProviders VALUES ('{s2}', 'Imdb', 'tt9990301');
        """,
        userdata="\n".join(
            [
                _userdata_sql(e1, e1.lower(), "2024-03-01T10:00:00.0000000Z"),
                _userdata_sql(PH, "tt9990301001001", "2024-03-02T10:00:00.0000000Z"),
            ]
        ),
    )
    bundle = export_history_bundle(jellyfin_db=db, user_id=USER)
    assert any(r.confiance == "probable" for r in bundle.rows)


def test_journal_reidentification_merges_episodes(tmp_path):
    """ItemId sur A + démarrage = LastPlayedDate de B (même S/E) → fusion."""
    a = "aaaaaaaa-1111-1111-1111-111111111111"
    b = "bbbbbbbb-2222-2222-2222-222222222222"
    t_a = t_b = "2024-04-01T10:00:00.0000000Z"
    db = tmp_path / "j.db"
    _efcore_db(
        db,
        userdata="\n".join(
            [
                _userdata_sql(PH, "990401001001", t_a),
                _userdata_sql(PH, a, t_a),
                _userdata_sql(PH, "tt9990001001001", t_b),
                _userdata_sql(PH, b, t_b),
            ]
        ),
        activity=(
            "INSERT INTO ActivityLogs VALUES (1, 'VideoPlayback', "
            f"'demo est en train de lire Serie X - S01E01 sur TV', '{USER}', '{a}', '{t_a}');"
        ),
    )
    t = load(str(db))
    stats = __import__("collections").Counter()
    elements, _ = build(t, stats)
    events = associate_logs(t, elements, stats)
    entries, _, _ = finalize(t, elements, events, stats)
    ep_rows = [e for e in entries if e.get("type") == "episode" and e.get("saison") == 1 and e.get("episode") == 1]
    assert len(ep_rows) == 1


def test_start_window_links_not_stop_window(tmp_path):
    """±2 s sur VideoPlayback oui ; arrêt tardif ne doit pas créer de second épisode."""
    ts = "2024-05-01T10:00:00.0000000Z"
    stop = "2024-05-01T10:45:00.0000000Z"
    db = tmp_path / "j.db"
    _efcore_db(
        db,
        userdata=_userdata_sql(PH, "990501001003", ts),
        activity="\n".join(
            [
                (
                    "INSERT INTO ActivityLogs VALUES (1, 'VideoPlayback', "
                    f"'demo est en train de lire Serie Demo - Ep S01E03 sur TV', '{USER}', NULL, '{ts}');"
                ),
                (
                    "INSERT INTO ActivityLogs VALUES (2, 'VideoPlaybackStopped', "
                    "'demo vient d''arrêter la lecture de Serie Demo - Ep S01E03 sur TV', "
                    f"'{USER}', NULL, '{stop}');"
                ),
            ]
        ),
    )
    bundle = export_history_bundle(jellyfin_db=db, user_id=USER)
    assert len(bundle.rows) == 1
    assert bundle.rows[0].season == 1 and bundle.rows[0].episode == 3


def test_export_performance_large_synthetic(tmp_path):
    """~7 500 UserData + ~6 400 logs : export < 15 s."""
    import time

    db = tmp_path / "big.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        f"""
        CREATE TABLE Users (Id TEXT PRIMARY KEY, Username TEXT NOT NULL);
        INSERT INTO Users VALUES ('{USER}', 'demo');
        CREATE TABLE BaseItems (
            Id TEXT PRIMARY KEY, Type TEXT, Name TEXT, ProductionYear INTEGER,
            SeriesName TEXT, ParentIndexNumber INTEGER, IndexNumber INTEGER,
            SeriesId TEXT, IsMovie INTEGER, RunTimeTicks INTEGER
        );
        CREATE TABLE BaseItemProviders (
            ItemId TEXT, ProviderId TEXT, ProviderValue TEXT,
            PRIMARY KEY (ItemId, ProviderId)
        );
        CREATE TABLE UserData (
            ItemId TEXT, UserId TEXT, CustomDataKey TEXT, Played INTEGER, PlayCount INTEGER,
            LastPlayedDate TEXT, PRIMARY KEY (ItemId, UserId, CustomDataKey)
        );
        CREATE TABLE ActivityLogs (
            Id INTEGER PRIMARY KEY AUTOINCREMENT, Type TEXT, Name TEXT, UserId TEXT,
            ItemId TEXT, DateCreated TEXT
        );
        """
    )
    movie = "22222222-2222-2222-2222-222222222222"
    conn.execute(
        "INSERT INTO BaseItems VALUES (?, ?, ?, ?, NULL, NULL, NULL, NULL, 1, NULL)",
        (movie, TYPE_MOVIE, "Film Perf", 2020),
    )
    conn.execute(
        "INSERT INTO BaseItemProviders VALUES (?, 'Tmdb', '900001')",
        (movie,),
    )
    ud = []
    for i in range(7500):
        ud.append((movie, USER, f"perf-key-{i}", 1, 1, f"2024-01-{(i % 28) + 1:02d}T12:00:00.0000000Z"))
    conn.executemany(
        "INSERT INTO UserData (ItemId, UserId, CustomDataKey, Played, PlayCount, LastPlayedDate) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ud,
    )
    logs = []
    for i in range(6400):
        kind = "VideoPlayback" if i % 2 == 0 else "VideoPlaybackStopped"
        played = f"2024-02-{(i % 28) + 1:02d}T12:00:00.0000000Z"
        logs.append((kind, "demo est en train de lire Film Perf sur TV", USER, movie, played))
    conn.executemany(
        "INSERT INTO ActivityLogs (Type, Name, UserId, ItemId, DateCreated) VALUES (?, ?, ?, ?, ?)",
        logs,
    )
    conn.commit()
    conn.close()
    started = time.perf_counter()
    export_history_bundle(jellyfin_db=db, user_id=USER)
    elapsed = time.perf_counter() - started
    assert elapsed < 15.0, f"export trop lent : {elapsed:.2f}s"
