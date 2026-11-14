"""Pagination médias, import par lots, concurrence SQLite, origine refollow."""

from __future__ import annotations

import subprocess
import threading
import time
from datetime import timezone
from io import BytesIO
from pathlib import Path

from sqlalchemy import delete, func, select
from tests.conftest import csrf, login
from tests.test_jellyfin_history_import import NOW, _auth, _csv_row

from mediatracker.db.models import Media, MediaItem, Setting
from mediatracker.integrations import jellyfin_history_import as jhi
from mediatracker.integrations.jellyfin_history_import import (
    FOLLOW_ORIGIN,
    apply_import,
    parse_csv_text,
    refollow_imported,
)
from mediatracker.integrations.tmdb import TmdbTitle

ROOT = Path(__file__).resolve().parents[1]
TSX = ROOT / "frontend" / "node_modules" / ".bin" / "tsx"


def _csv_lines_movies(count: int, *, tmdb_base: int = 890000) -> list:
    header = ",".join(jhi.CSV_COLUMNS)
    rows = []
    for idx in range(count):
        line = _csv_row(
            title=f"Film créé {idx + 1}",
            tmdb_id=str(tmdb_base + idx),
            jellyfin_item_id=f"jf-create-{idx}",
        ).strip().split("\n", 1)[1]
        rows.append(line)
    return parse_csv_text(header + "\n" + "\n".join(rows) + "\n")


def _csv_lines_mixed(*, films: int, episodes: int, tmdb_base: int = 891000) -> list:
    header = ",".join(jhi.CSV_COLUMNS)
    rows = []
    for idx in range(films):
        line = _csv_row(
            title=f"Film créé {idx + 1}",
            tmdb_id=str(tmdb_base + idx),
            jellyfin_item_id=f"jf-film-{idx}",
        ).strip().split("\n", 1)[1]
        rows.append(line)
    series_count = max(1, episodes // 10)
    for idx in range(episodes):
        series_idx = idx % series_count
        line = _csv_row(
            type="episode",
            title=f"Épisode {idx + 1}",
            series_title=f"Série créée {series_idx + 1}",
            year="2021",
            season=str(1 + (idx // 10) % 3),
            episode=str(1 + idx % 10),
            tmdb_id=str(tmdb_base + 10_000 + idx),
            jellyfin_item_id=f"jf-ep-{idx}",
        ).strip().split("\n", 1)[1]
        rows.append(line)
    return parse_csv_text(header + "\n" + "\n".join(rows) + "\n")


def _fake_tmdb_fetch(monkeypatch) -> None:
    def fake_maybe_fetch(
        db, settings, secret_key, tmdb_id, media_type, *, tmdb_cache=None, import_tx=None, **kwargs
    ):
        del db, settings, secret_key
        if import_tx is not None:
            import_tx.release_before_network()
        key = (media_type, tmdb_id)
        if tmdb_cache is not None and key in tmdb_cache:
            return tmdb_cache[key]
        if media_type == "series":
            title = f"Série TMDB {tmdb_id}"
        else:
            title = f"Film TMDB {tmdb_id}"
        fetched = TmdbTitle(tmdb_id, media_type, title, 2020, None, None, None, None, ())
        if tmdb_cache is not None:
            tmdb_cache[key] = fetched
        return fetched

    monkeypatch.setattr(jhi, "_maybe_fetch", fake_maybe_fetch)


def _media_snapshot(db) -> list[tuple]:
    rows = list(db.scalars(select(Media).order_by(Media.id)).all())
    return [
        (row.type, row.title, row.year, row.tmdb_id, row.follow_origin, bool(row.followed))
        for row in rows
    ]


def _items_snapshot(db) -> list[tuple]:
    rows = list(db.scalars(select(MediaItem).order_by(MediaItem.id)).all())
    out = []
    for row in rows:
        watched_at = row.watched_at
        if watched_at is not None and watched_at.tzinfo is None:
            watched_at = watched_at.replace(tzinfo=timezone.utc)
        iso = watched_at.isoformat() if watched_at else None
        media = db.get(Media, row.media_id)
        origin = media.follow_origin if media else None
        out.append(
            (
                row.media_id,
                bool(row.watched),
                iso,
                row.season,
                row.episode,
                origin,
                row.jellyfin_item_id,
            )
        )
    return out


def _full_snapshot(db) -> dict[str, list[tuple]]:
    return {"media": _media_snapshot(db), "items": _items_snapshot(db)}


def _reset_media(db) -> None:
    db.execute(delete(MediaItem))
    db.execute(delete(Media))
    db.execute(delete(Setting).where(Setting.key == jhi.FOLLOW_CANDIDATES_KEY))
    db.commit()


def test_media_list_cursor_pagination(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        for idx in range(55):
            media = Media(
                type="film",
                title=f"Film exemple {idx + 1}",
                year=2020,
                tmdb_id=990000 + idx,
                followed=True,
                created_at=NOW,
            )
            db.add(media)
            db.flush()
            db.add(MediaItem(media_id=media.id, title=media.title, state="missing"))
        db.commit()
    finally:
        db.close()

    page1 = client.get("/api/v1/media?limit=50")
    assert page1.status_code == 200
    body1 = page1.json()
    assert len(body1["items"]) == 50
    assert body1["next_cursor"] is not None

    stuck = client.get(f"/api/v1/media?limit=50&cursor={body1['next_cursor']}")
    assert stuck.status_code == 200
    repeat = client.get(f"/api/v1/media?limit=50&cursor={body1['next_cursor']}")
    assert repeat.status_code == 200
    assert {row["id"] for row in stuck.json()["items"]} == {row["id"] for row in repeat.json()["items"]}

    page2 = client.get(f"/api/v1/media?limit=50&cursor={body1['next_cursor']}")
    assert len(page2.json()["items"]) == 5
    assert page2.json()["next_cursor"] is None


def test_media_list_cursor_guard_rejects_repeated_cursor():
    assert TSX.is_file(), "Installez le frontend (npm ci) pour exécuter le garde-fou TS."
    proc = subprocess.run(
        [str(TSX), "tests/media_list_cursor_guard.mts"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_apply_import_many_lines_batch_matches_monolith_and_idempotent(client, app, monkeypatch):
    settings = app.state.settings
    lines = _csv_lines_mixed(films=180, episodes=120, tmdb_base=892000)
    _fake_tmdb_fetch(monkeypatch)
    monkeypatch.setattr(jhi, "IMPORT_COMMIT_BATCH", 25)
    monkeypatch.setattr(jhi, "IMPORT_COMMIT_MAX_INTERVAL_S", 0.01)

    db = app.state.session_factory()
    try:
        _reset_media(db)
        batched = apply_import(db, settings, settings.secret_key, lines, now=NOW)
        db.commit()
        assert batched.applied == len(lines)
        assert batched.created_media > 0
        assert batched.commits_persisted >= 3
        snap_batch = _full_snapshot(db)
        media_count = db.scalar(select(func.count()).select_from(Media)) or 0
        item_count = db.scalar(select(func.count()).select_from(MediaItem)) or 0
    finally:
        db.close()

    db = app.state.session_factory()
    try:
        again = apply_import(db, settings, settings.secret_key, lines, now=NOW)
        db.commit()
        assert again.applied == 0
        assert again.created_media == 0
        assert db.scalar(select(func.count()).select_from(Media)) == media_count
        assert db.scalar(select(func.count()).select_from(MediaItem)) == item_count
    finally:
        db.close()

    db = app.state.session_factory()
    try:
        _reset_media(db)
    finally:
        db.close()

    def _noop_import_commit(self) -> None:
        del self

    monkeypatch.setattr(jhi._ImportTx, "_commit", _noop_import_commit)
    db = app.state.session_factory()
    try:
        mono = apply_import(db, settings, settings.secret_key, lines, now=NOW)
        db.commit()
        assert mono.applied == len(lines)
        assert mono.commits_persisted == 0
        snap_mono = _full_snapshot(db)
    finally:
        db.close()

    assert snap_batch == snap_mono


CONCURRENT_WRITE_MAX_S = 1.0


def _run_concurrent_import_write_scenario(
    client,
    app,
    monkeypatch,
    *,
    disable_import_commits: bool,
) -> tuple[list[str], int]:
    from mediatracker.core.tmdb import fetch as tmdb_fetch_module

    settings = app.state.settings
    line_count = 40
    lines = _csv_lines_movies(line_count, tmdb_base=894000)
    fetch_calls: list[tuple[int, str]] = []
    gate = threading.Event()

    def slow_fetch_title(db, settings_arg, tmdb_id, media_type):
        del db, settings_arg
        fetch_calls.append((tmdb_id, media_type))
        gate.wait(timeout=5)
        time.sleep(0.12)
        return TmdbTitle(tmdb_id, media_type, f"Film TMDB {tmdb_id}", 2020, None, None, None, None, ())

    monkeypatch.setattr(tmdb_fetch_module, "fetch_title", slow_fetch_title)
    monkeypatch.setattr(jhi, "IMPORT_COMMIT_BATCH", 4)
    monkeypatch.setattr(jhi, "IMPORT_COMMIT_MAX_INTERVAL_S", 0.01)
    if disable_import_commits:

        def _noop_import_commit(self) -> None:
            del self

        monkeypatch.setattr(jhi._ImportTx, "_commit", _noop_import_commit)

    errors: list[str] = []
    login(client)
    write_headers = csrf(client)

    def run_import() -> None:
        session = app.state.session_factory()
        try:
            apply_import(session, settings, settings.secret_key, lines, now=NOW)
            session.commit()
        except Exception as exc:
            errors.append(str(exc))
        finally:
            session.close()

    worker = threading.Thread(target=run_import)
    worker.start()
    time.sleep(0.05)
    gate.set()
    for idx in range(25):
        started = time.monotonic()
        response = client.post(
            "/api/v1/media",
            json={"type": "film", "title": f"Film concurrent {idx}", "tmdb_id": 894900 + idx},
            headers=write_headers,
        )
        elapsed = time.monotonic() - started
        if response.status_code not in {200, 201}:
            errors.append(f"write status {response.status_code}: {response.text[:200]}")
        elif elapsed >= CONCURRENT_WRITE_MAX_S:
            errors.append(f"write {idx} blocked {elapsed:.2f}s (>={CONCURRENT_WRITE_MAX_S}s)")
        time.sleep(0.02)
    worker.join(timeout=120)
    if worker.is_alive():
        errors.append("import thread still running")
    return errors, len(fetch_calls)


def test_concurrent_write_during_slow_tmdb_fetch(client, app, monkeypatch):
    """Avec _ImportTx : écritures API pendant TMDB lentes restent < 1 s."""
    errors, fetch_count = _run_concurrent_import_write_scenario(
        client,
        app,
        monkeypatch,
        disable_import_commits=False,
    )
    assert not errors, errors
    assert fetch_count == 40


def test_concurrent_write_fails_without_import_tx_commits(client, app, monkeypatch):
    """Sans commits intermédiaires, l'import retient SQLite trop longtemps."""
    errors, _ = _run_concurrent_import_write_scenario(
        client,
        app,
        monkeypatch,
        disable_import_commits=True,
    )
    assert errors, "expected slow writes or HTTP errors without _ImportTx._commit"


def test_refollow_keeps_jellyfin_import_origin(client, app):
    headers = _auth(client)
    imported = client.post(
        "/api/v1/library/jellyfin-history/import?dry_run=false&sync=true",
        headers=headers,
        files={"upload": ("hist.csv", BytesIO(_csv_row().encode()), "text/csv")},
    )
    assert imported.status_code == 200
    media_id = imported.json()["follow_candidates"][0]["id"]
    refollow = client.post(
        "/api/v1/library/jellyfin-history/refollow",
        headers=headers,
        json={"media_ids": [media_id]},
    )
    assert refollow.json()["updated"] == 1
    db = app.state.session_factory()
    try:
        row = db.get(Media, media_id)
        assert row.followed is True
        assert row.follow_origin == FOLLOW_ORIGIN
    finally:
        db.close()


def test_refollow_resets_identification_origin(client, app):
    db = app.state.session_factory()
    try:
        media = Media(
            type="film",
            title="Film identifié",
            year=2020,
            tmdb_id=880777,
            followed=False,
            follow_origin="identification",
            created_at=NOW,
        )
        db.add(media)
        db.flush()
        db.add(MediaItem(media_id=media.id, title=media.title, state="missing"))
        db.commit()
        updated = refollow_imported(db, [media.id])
        db.commit()
        assert updated == 1
        db.refresh(media)
        assert media.follow_origin == "user"
    finally:
        db.close()


def test_refollow_imported_unit(client, app):
    db = app.state.session_factory()
    try:
        media = Media(
            type="film",
            title="Film Démo",
            year=2020,
            tmdb_id=880888,
            followed=False,
            follow_origin=FOLLOW_ORIGIN,
            created_at=NOW,
        )
        db.add(media)
        db.flush()
        db.add(MediaItem(media_id=media.id, title=media.title, state="missing"))
        db.commit()
        updated = refollow_imported(db, [media.id])
        db.commit()
        assert updated == 1
        db.refresh(media)
        assert media.follow_origin == FOLLOW_ORIGIN
    finally:
        db.close()
