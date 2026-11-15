"""Queue owner: downloads, library, Jellyfin, sources, notifications, and cleanup."""

import fcntl
import logging
import signal
import threading
from pathlib import Path

from sqlalchemy.exc import OperationalError

from mediatracker.config import get_settings
from mediatracker.core.aria2_events import Aria2EventListener
from mediatracker.core.aria2_reconcile import maybe_reconcile_aria2
from mediatracker.core.catalog import maybe_refresh_catalog
from mediatracker.core.cleanup import maintenance
from mediatracker.core.dead_links import maybe_reverify_dead_links
from mediatracker.core.digital_releases import maybe_refresh_digital_releases
from mediatracker.core.incomplete import maybe_clean_incomplete
from mediatracker.core.metrics.history import maybe_record_metrics_history
from mediatracker.core.metrics.worker_counters import maybe_persist_worker_metrics
from mediatracker.core.pipeline import close_cached_clients, tick
from mediatracker.core.seasons import maybe_refresh_seasons
from mediatracker.core.source_poll_tick import pump_forced_source_check_worker, worker_poll_if_due
from mediatracker.core.source_state import flush_pending_source_observations, maybe_purge_old_changes
from mediatracker.core.worker_schedule import ACTIVE_TICK_SECONDS, compute_sleep_seconds
from mediatracker.core.worker_wake import WorkerWaiter
from mediatracker.db.session import commit_retry, create_session_factory, is_sqlite_locked, rollback_if_inactive
from mediatracker.events.deliver import deliver_due
from mediatracker.events.retention import maybe_purge_old_events
from mediatracker.hive.catalog import sync_hive_catalog
from mediatracker.hive.gossip_worker import run_worker_hive_gossip_isolated
from mediatracker.integrations.jellyfin_sync import maybe_sync_jellyfin
from mediatracker.integrations.tmdb_images import maybe_purge_cache
from mediatracker.library.inotify_watch import LibraryInotifyWatcher
from mediatracker.library.jellyfin_align import maybe_align_jellyfin
from mediatracker.library.jellyfin_push import maybe_push_jellyfin
from mediatracker.library.scanner import maybe_scan_library
from mediatracker.maintenance.backup import maybe_backup
from mediatracker.maintenance.sqlite_tune import maybe_maintain_sqlite
from mediatracker.maintenance.worker_gate import acknowledge_worker_maintenance, worker_maintenance_active
from mediatracker.security.logging import configure_logging

logger = logging.getLogger(__name__)

# Active aria2 jobs still need the legacy 2 s poll cadence for progress and ETA.
TICK_SECONDS = int(ACTIVE_TICK_SECONDS)
_LOCK = None


def _maybe_savepoint(db, label: str, fn) -> None:
    """Flush-only step: isolate failures with a SAVEPOINT."""
    try:
        with db.begin_nested():
            fn()
    except OperationalError as exc:
        if is_sqlite_locked(exc):
            logger.warning("%s : base occupée, nouvel essai au prochain passage.", label)
            rollback_if_inactive(db)
            return
        logger.exception("%s a échoué ; le passage continue.", label)
        rollback_if_inactive(db)
    except Exception:
        logger.exception("%s a échoué ; le passage continue.", label)


def _maybe_session(db, label: str, fn) -> None:
    """Step that may commit, rollback, or run SQLite maintenance: no SAVEPOINT."""
    try:
        fn()
    except Exception:
        logger.exception("%s a échoué ; le passage continue.", label)
        db.rollback()


def _catchup_cherche_signals(db, settings) -> None:
    from datetime import datetime, timezone

    from mediatracker.hive.signal_policy import maybe_catchup_cherche_for_followed

    maybe_catchup_cherche_for_followed(db, settings, now=datetime.now(timezone.utc))


def _hive_gossip_isolated(factory, settings) -> None:
    run_worker_hive_gossip_isolated(factory, settings)


def _poll_sources_isolated(factory, settings) -> None:
    try:
        if pump_forced_source_check_worker(factory, settings):
            return
        worker_poll_if_due(factory, settings)
    except OperationalError as exc:
        if is_sqlite_locked(exc):
            logger.warning("Sondage sources : base occupée, nouvel essai au prochain passage.")
        else:
            logger.exception("Sondage sources a échoué.")
    except Exception:
        logger.exception("Sondage sources a échoué.")


def _run_worker_pass(db, settings, factory) -> None:
    """One worker iteration body; failures are isolated per step."""
    _maybe_savepoint(db, "Réconciliation aria2", lambda: maybe_reconcile_aria2(db, settings))
    # tick() commits while starting pending downloads: no SAVEPOINT here.
    _maybe_session(db, "Pipeline", lambda: tick(db, settings, commit=False))
    _maybe_session(db, "Scan bibliothèque", lambda: maybe_scan_library(db, settings))
    _maybe_session(db, "Synchro Jellyfin", lambda: maybe_sync_jellyfin(db, settings))
    _maybe_session(db, "Alignement Jellyfin", lambda: maybe_align_jellyfin(db, settings))
    _maybe_session(db, "Push Jellyfin", lambda: maybe_push_jellyfin(db, settings))
    commit_retry(db)
    _poll_sources_isolated(factory, settings)
    _maybe_session(db, "Hive catalogue", lambda: sync_hive_catalog(db, settings))
    _maybe_savepoint(
        db,
        "Hive signaux cherche (rattrapage)",
        lambda: _catchup_cherche_signals(db, settings),
    )
    commit_retry(db)
    _hive_gossip_isolated(factory, settings)
    _maybe_session(db, "Catalogue", lambda: maybe_refresh_catalog(db, settings))
    _maybe_session(db, "Sorties numériques", lambda: maybe_refresh_digital_releases(db, settings))
    _maybe_savepoint(db, "Liens morts", lambda: maybe_reverify_dead_links(db, settings))
    _maybe_session(db, "Saisons", lambda: maybe_refresh_seasons(db, settings))
    _maybe_session(db, "Maintenance", lambda: maintenance(db, settings))
    _maybe_savepoint(db, "Purge événements", lambda: maybe_purge_old_events(db))
    _maybe_savepoint(db, "Purge changements sources", lambda: maybe_purge_old_changes(db))
    _maybe_savepoint(db, "Observations sources", lambda: flush_pending_source_observations(db))
    _maybe_savepoint(db, "Notifications", lambda: deliver_due(db, settings, commit=False))
    _maybe_session(db, "Sauvegarde", lambda: maybe_backup(settings, db))
    _maybe_savepoint(db, "Purge cache TMDB", lambda: maybe_purge_cache(settings, db))
    _maybe_session(db, "SQLite", lambda: maybe_maintain_sqlite(db, settings))
    history_recorded = [False]

    def _record_history() -> None:
        history_recorded[0] = maybe_record_metrics_history(db, settings)

    _maybe_savepoint(db, "Historique métriques", _record_history)
    _maybe_savepoint(
        db,
        "Métriques worker",
        lambda: maybe_persist_worker_metrics(db, force=history_recorded[0]),
    )
    _maybe_savepoint(db, "Métriques worker (tampon)", lambda: maybe_persist_worker_metrics(db))


def compute_worker_pause(factory, settings) -> float:
    """Idle sleep before the next loop. Falls back to the active tick on scheduler errors."""
    sleep_db = factory()
    try:
        return compute_sleep_seconds(sleep_db, settings)
    except Exception:
        logger.exception("Calcul du sommeil worker impossible, repli sur %s s.", ACTIVE_TICK_SECONDS)
        return ACTIVE_TICK_SECONDS
    finally:
        sleep_db.close()


def _claim_worker(config_dir: Path) -> bool:
    """One scheduler per config directory. The API process does not take this lock."""
    global _LOCK
    config_dir.mkdir(parents=True, exist_ok=True)
    handle = (config_dir / "worker.lock").open("a", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False
    _LOCK = handle
    return True


def main() -> None:
    settings = get_settings()
    configure_logging(
        settings.log_level,
        Path(settings.config_dir) / "logs",
        max_bytes=settings.log_max_bytes,
        backup_count=settings.log_backup_count,
    )
    if not _claim_worker(Path(settings.config_dir)):
        logger.error("Un worker est déjà en cours. Ce processus s'arrête.")
        return
    _engine, factory = create_session_factory(settings)
    stop = threading.Event()

    def _stop(signum, _frame) -> None:
        logger.info("Signal %s reçu, arrêt du worker.", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    logger.info(
        "Worker démarré. Suivi aria2 actif toutes les %s s ; sinon sommeil jusqu'à la prochaine "
        "échéance (filet %s s max). "
        "Bibliothèque locale : repasse selon l'intervalle (défaut 24 h). "
        "Jellyfin : refresh après dépôt, synchro « vu » (webhook ou toutes les %s min). "
        "Sources : un média dû à la fois, pas avant la date de diffusion, intervalle %s min. "
        "Nouveautés : au plus deux fois par jour. "
        "Notifications et corbeille à chaque passage.",
        TICK_SECONDS,
        60,
        settings.jellyfin_poll_interval_min,
        settings.scan_interval_min,
    )
    waiter = WorkerWaiter(Path(settings.config_dir), stop)
    waiter.start()
    aria2_events = Aria2EventListener(settings, factory, stop)
    aria2_events.start()
    inotify = LibraryInotifyWatcher(settings, factory, stop)
    inotify.start()
    while not stop.is_set():
        try:
            db = factory()
            try:
                if worker_maintenance_active(db):
                    acknowledge_worker_maintenance(db)
                    commit_retry(db)
                    waiter.wait(1.0)
                    continue
                _run_worker_pass(db, settings, factory)
                commit_retry(db)
            finally:
                db.close()
        except OperationalError as exc:
            if is_sqlite_locked(exc):
                logger.warning("Passage de la file reporté (base occupée).")
            else:
                logger.exception("Passage de la file échoué.")
        except Exception:
            logger.exception("Passage de la file échoué.")
        # Own session: a failure earlier in the tick must not skip the hourly cleanup.
        try:
            cleanup = factory()
            try:
                maybe_clean_incomplete(cleanup, settings)
            finally:
                cleanup.close()
        except Exception:
            logger.exception("Nettoyage des téléchargements incomplets échoué.")
        waiter.wait(compute_worker_pause(factory, settings))
    aria2_events.stop()
    from mediatracker.core.outbound_http import shutdown_outbound_integrations

    close_cached_clients()
    shutdown_outbound_integrations()
    try:
        shutdown_db = factory()
        try:
            maybe_persist_worker_metrics(shutdown_db, commit=True, force=True)
        finally:
            shutdown_db.close()
    except Exception:
        logger.exception("Flush des compteurs worker à l'arrêt échoué.")
    logger.info("Worker arrêté.")


if __name__ == "__main__":
    main()
