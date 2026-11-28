"""Mesure passe worker complète (protocole relecture, base de test uniquement)."""

from __future__ import annotations

import os
import random
import statistics
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text, update
from tests.conftest import login

from mediatracker.core.queue_hold import set_downloads_held
from mediatracker.db.models import Download, Media, MediaItem, Provider, SourceBinding, TaskRun
from mediatracker.db.session import commit_retry
from mediatracker.metro.collection import set_collection_duration
from mediatracker.metro.record import (
    flush_busy_wait_ms,
    flush_pass_buffer,
    load_pass_collection_flag,
    record_blocked_continuous,
    reset_flush_metrics,
    reset_metro_instrumentation_state,
)
from mediatracker.worker import _run_worker_pass


@pytest.fixture(autouse=True)
def _reset_metro_bench():
    reset_metro_instrumentation_state()
    yield
    reset_metro_instrumentation_state()


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = int(round((len(ordered) - 1) * pct))
    return ordered[idx]


@dataclass
class _WriterStats:
    locks_held: int = 0
    errors: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)


def _seed_bench_media(factory) -> None:
    """Médias suivis + bindings (hors téléchargements), avant échauffement."""
    with factory() as db:
        db.execute(text("PRAGMA journal_mode=WAL"))
        now = datetime.now(timezone.utc)
        past = now - timedelta(hours=2)
        provider = db.scalars(select(Provider).where(Provider.type == "wawacity")).first()
        if provider is None:
            db.commit()
            return
        if db.scalar(select(Media.id).where(Media.title.like("Bench fictif poll%")).limit(1)) is None:
            for i in range(3):
                media = Media(
                    type="film",
                    title=f"Bench fictif poll {i}",
                    followed=True,
                    next_source_poll_at=past,
                    created_at=now,
                )
                db.add(media)
                db.flush()
                item = MediaItem(media_id=media.id, state="missing")
                db.add(item)
                db.add(
                    SourceBinding(
                        media_id=media.id,
                        provider_id=provider.id,
                        external_ref=f"bench-{i}",
                        url="https://mediatracker.example.com/catalog/bench",
                        path="/catalog/bench",
                        active=True,
                    )
                )
        if db.scalar(select(Media.id).where(Media.title.like("Bench fictif %")).limit(1)) is None:
            for i in range(120):
                db.add(
                    Media(
                        type="film",
                        title=f"Bench fictif {i}",
                        followed=False,
                        created_at=now,
                    )
                )
        db.commit()


def _reseed_bench_downloads(factory) -> None:
    """Téléchargements pending / post_processing — avant chaque passe active."""
    with factory() as db:
        items = db.scalars(select(MediaItem).join(Media).where(Media.title.like("Bench fictif poll%")).limit(3)).all()
        if len(items) < 3:
            db.commit()
            return
        states = ["pending", "pending", "post_processing"]
        for item, state in zip(items, states, strict=True):
            existing_id = db.scalar(select(Download.id).where(Download.media_item_id == item.id).limit(1))
            if existing_id is not None:
                db.execute(
                    update(Download).where(Download.id == existing_id).values(state=state, progress=10, dismissed=False)
                )
            else:
                db.add(Download(media_item_id=item.id, state=state, progress=10))
        db.commit()


def _background_writer_holding(factory, stop: threading.Event, stats: _WriterStats) -> None:
    rng = random.Random(42)
    while not stop.is_set():
        db = factory()
        try:
            with db.begin():
                db.execute(text("UPDATE setting SET updated_at = updated_at WHERE key = 'task_run.collection_until'"))
                db.flush()
                with stats.lock:
                    stats.locks_held += 1
                time.sleep(rng.uniform(0.05, 0.2))
        except Exception as exc:
            with stats.lock:
                stats.errors.append(f"{type(exc).__name__}: {exc}")
            db.rollback()
            raise
        finally:
            db.close()
        stop.wait(0.5)


_pass_serial = {"n": 0}


def _single_pass(
    factory,
    settings,
    *,
    collect: bool,
) -> tuple[float, int, float, float]:
    _pass_serial["n"] += 1
    t_seed = time.perf_counter()
    _reseed_bench_downloads(factory)
    seed_s = time.perf_counter() - t_seed
    with factory() as db:
        set_collection_duration(db, "1h" if collect else "off")
        set_downloads_held(db, _pass_serial["n"] % 3 == 0)
        db.commit()
    load_pass_collection_flag(factory)
    if collect:
        with factory() as count_db:
            task_run_before = count_db.scalar(select(func.count()).select_from(TaskRun)) or 0
    else:
        task_run_before = 0
    t0 = time.perf_counter()
    db = factory()
    try:
        _run_worker_pass(db, settings, factory)
        commit_retry(db)
        from mediatracker.metro.instrument import instrument_worker_pass_soc_unblock
        from mediatracker.metro.record import resolve_deferred_instruments, safe_instrument

        safe_instrument(instrument_worker_pass_soc_unblock)
        resolve_deferred_instruments()
        t_flush = time.perf_counter()
        flush_pass_buffer(factory)
        flush_s = time.perf_counter() - t_flush
    finally:
        db.close()
    if collect:
        with factory() as count_db:
            task_run_after = count_db.scalar(select(func.count()).select_from(TaskRun)) or 0
        lines = int(task_run_after - task_run_before)
    else:
        lines = 0
    return time.perf_counter() - t0, lines, flush_s, seed_s


def test_worker_full_pass_timing_report(client, app, monkeypatch):
    """Protocole : WAL, jeu semé, 2e écrivain, échauffement + 200 passes ABAB."""
    login(client)
    factory = app.state.session_factory
    settings = app.state.settings
    _seed_bench_media(factory)

    def fake_worker_poll(factory, settings, **kwargs):
        from mediatracker.metro.instrument import instrument_source_poll

        instrument_source_poll(did_work=True)
        return SimpleNamespace()

    monkeypatch.setattr("mediatracker.worker.worker_poll_if_due", fake_worker_poll)
    monkeypatch.setattr(
        "mediatracker.worker.pump_forced_source_check_worker",
        lambda *args, **kwargs: False,
    )

    warmup = int(os.environ.get("MEDIATRACKER_BENCH_WARMUP", "20"))
    rounds = int(os.environ.get("MEDIATRACKER_BENCH_ROUNDS", "200"))
    stop = threading.Event()
    writer_stats = _WriterStats()
    writer = threading.Thread(
        target=_background_writer_holding,
        args=(factory, stop, writer_stats),
        daemon=True,
    )
    writer.start()
    try:
        reset_flush_metrics()
        _pass_serial["n"] = 0
        for _ in range(warmup):
            _single_pass(factory, settings, collect=False)
        off_d: list[float] = []
        on_d: list[float] = []
        off_lines: list[int] = []
        on_lines: list[int] = []
        on_flush: list[float] = []
        off_seed: list[float] = []
        on_seed: list[float] = []
        for i in range(rounds):
            collect = (i % 2) == 1
            duration, lines, flush_s, seed_s = _single_pass(factory, settings, collect=collect)
            if collect:
                on_d.append(duration)
                on_lines.append(lines)
                on_flush.append(flush_s)
                on_seed.append(seed_s)
            else:
                off_d.append(duration)
                off_lines.append(lines)
                off_seed.append(seed_s)
        flush_over_20ms = sum(1 for value in on_flush if value > 0.020)
        with writer_stats.lock:
            locks_held = writer_stats.locks_held
            writer_errors = list(writer_stats.errors)
        report = {
            "warmup_passes": warmup,
            "rounds_per_mode": rounds // 2,
            "background_writer_locks_held": locks_held,
            "background_writer_errors": writer_errors,
            "collect_off": {
                "median_s": round(statistics.median(off_d), 6),
                "p95_s": round(_percentile(off_d, 0.95), 6),
                "max_s": round(max(off_d), 6),
                "lines_per_pass_median": int(statistics.median(off_lines)),
            },
            "collect_on": {
                "median_s": round(statistics.median(on_d), 6),
                "p95_s": round(_percentile(on_d, 0.95), 6),
                "max_s": round(max(on_d), 6),
                "lines_per_pass_median": int(statistics.median(on_lines)),
                "flush_median_s": round(statistics.median(on_flush), 6),
                "flush_p95_s": round(_percentile(on_flush, 0.95), 6),
                "flush_max_s": round(max(on_flush), 6),
                "flush_over_20ms_count": flush_over_20ms,
            },
            "flush_busy_wait_ms_total": round(flush_busy_wait_ms(), 3),
            "reseed_downloads_median_s": {
                "collect_off": round(statistics.median(off_seed), 6) if off_seed else 0.0,
                "collect_on": round(statistics.median(on_seed), 6) if on_seed else 0.0,
            },
            "pass_gap_median_s": round(
                statistics.median(on_d) - statistics.median(off_d), 6,
            )
            if on_d and off_d
            else 0.0,
        }
        print(report)
        assert locks_held > 0, f"2e écrivain n'a pris aucun verrou : {writer_errors}"
        assert not writer_errors, f"erreurs 2e écrivain : {writer_errors}"
        assert statistics.median(on_lines) > 0
        assert max(on_d) < 15.0
    finally:
        stop.set()
        writer.join(timeout=5.0)


def test_scenario_finalize_writes_without_long_busy_wait(client, app):
    login(client)
    from mediatracker.metro.instrument import instrument_download_started, instrument_finalize

    factory = app.state.session_factory
    with factory() as db:
        set_collection_duration(db, "1h")
        db.commit()
    load_pass_collection_flag(factory)
    reset_flush_metrics()
    instrument_download_started(7)
    instrument_finalize(7)
    t0 = time.perf_counter()
    flush_pass_buffer(factory)
    assert time.perf_counter() - t0 < 1.0
    assert flush_busy_wait_ms() < 500.0


def test_scenario_adb_block_one_line(client, app, monkeypatch):
    login(client)
    factory = app.state.session_factory
    with factory() as db:
        set_collection_duration(db, "1h")
        db.commit()
    load_pass_collection_flag(factory)
    times = iter([float(i) for i in range(0, 400, 70)])
    monkeypatch.setattr("mediatracker.metro.record._monotonic", lambda: next(times, 10_000.0))
    for _ in range(50):
        record_blocked_continuous(station="DL1", blocked_by="ADB1:AUTH_BLOCKED")
        flush_pass_buffer(factory)
        load_pass_collection_flag(factory)
    with factory() as db:
        rows = db.scalars(select(TaskRun).where(TaskRun.blocked_by == "ADB1:AUTH_BLOCKED")).all()
        assert len(rows) == 1


def test_scenario_collect_off_zero_writes_during_open_block(client, app):
    login(client)
    factory = app.state.session_factory
    with factory() as db:
        set_collection_duration(db, "1h")
        db.commit()
    load_pass_collection_flag(factory)
    record_blocked_continuous(station="DL1", blocked_by="DL9:held")
    flush_pass_buffer(factory)
    with factory() as db:
        before = len(db.scalars(select(TaskRun)).all())
    with factory() as db:
        set_collection_duration(db, "off")
        db.commit()
    load_pass_collection_flag(factory)
    record_blocked_continuous(station="DL1", blocked_by="DL9:held")
    assert flush_pass_buffer(factory) == 0
    with factory() as db:
        assert len(db.scalars(select(TaskRun)).all()) == before


def test_scenario_pass_with_uncommitted_writer_under_one_second(client, app, monkeypatch):
    login(client)
    factory = app.state.session_factory
    settings = app.state.settings
    _seed_bench_media(factory)
    _reseed_bench_downloads(factory)

    def fake_worker_poll(factory, settings, **kwargs):
        from mediatracker.metro.instrument import instrument_source_poll

        instrument_source_poll(did_work=True)
        return SimpleNamespace()

    monkeypatch.setattr("mediatracker.worker.worker_poll_if_due", fake_worker_poll)
    monkeypatch.setattr(
        "mediatracker.worker.pump_forced_source_check_worker",
        lambda *args, **kwargs: False,
    )

    with factory() as db:
        set_collection_duration(db, "1h")
        set_downloads_held(db, False)
        db.commit()
    load_pass_collection_flag(factory)

    lock_ready = threading.Event()
    pass_timed = threading.Event()
    held_during_pass = threading.Event()
    overlap_s = 0.3

    def hold_write() -> None:
        db = factory()
        try:
            with db.begin():
                db.execute(text("UPDATE download SET progress = progress WHERE id IS NOT NULL"))
                lock_ready.set()
                until = time.perf_counter() + overlap_s
                while time.perf_counter() < until:
                    if pass_timed.is_set():
                        held_during_pass.set()
                    time.sleep(0.005)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    holder = threading.Thread(target=hold_write, daemon=True)
    holder.start()
    assert lock_ready.wait(timeout=2.0), "l'écrivain n'a pas pris le verrou"
    pass_timed.set()
    t0 = time.perf_counter()
    with factory() as count_db:
        task_run_before = count_db.scalar(select(func.count()).select_from(TaskRun)) or 0
    db = factory()
    try:
        _run_worker_pass(db, settings, factory)
        commit_retry(db)
        from mediatracker.metro.instrument import instrument_worker_pass_soc_unblock
        from mediatracker.metro.record import resolve_deferred_instruments, safe_instrument

        safe_instrument(instrument_worker_pass_soc_unblock)
        resolve_deferred_instruments()
        flush_pass_buffer(factory)
    finally:
        db.close()
    duration = time.perf_counter() - t0
    with factory() as count_db:
        task_run_after = count_db.scalar(select(func.count()).select_from(TaskRun)) or 0
    lines = int(task_run_after - task_run_before)

    holder.join(timeout=2.0)
    assert held_during_pass.is_set(), "l'écrivain ne tenait pas le verrou pendant la passe chronométrée"
    assert duration < 1.0
    assert lines > 0
