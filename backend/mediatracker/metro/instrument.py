"""Branchements métro sur le pipeline existant (observateurs seulement)."""

from __future__ import annotations

from mediatracker.metro.graph import edge_between
from mediatracker.metro.record import (
    bind_download_sequence,
    clear_blocked,
    is_pass_collection_active,
    mark_pass_sqlite_locked,
    media_subject,
    note_sequence_station,
    parent_local_for_trigger,
    pass_saw_sqlite_locked,
    record_blocked_continuous,
    record_pass_idle_change,
    record_task_run,
    release_download_sequence,
    sequence_for_download,
)


def _seq(download_id: int) -> str:
    return sequence_for_download(download_id)


def instrument_pipeline_tick(
    *,
    held: bool,
    paused: bool,
    disk_low: bool,
) -> None:
    if paused:
        record_blocked_continuous(
            station="DL1",
            blocked_by="ADB1:AUTH_BLOCKED",
            cause="Compte AllDebrid bloqué",
        )
    else:
        clear_blocked("DL1", "ADB1:AUTH_BLOCKED")
    if disk_low:
        record_blocked_continuous(
            station="DL1",
            blocked_by="DL8:disk_low",
            cause="Espace disque insuffisant",
        )
    else:
        clear_blocked("DL1", "DL8:disk_low")
    if held:
        record_blocked_continuous(station="DL1", blocked_by="DL9:held", cause="Téléchargements en pause")
    else:
        clear_blocked("DL1", "DL9:held")


def instrument_download_started(
    download_id: int,
    row: object | None = None,
    *,
    from_station: str = "SRC8",
    media_id: int | None = None,
) -> None:
    bind_download_sequence(download_id, row)
    seq = _seq(download_id)
    local_id = record_task_run(
        station="DL1",
        trigger="event",
        outcome="ok",
        subject_ref=f"download:{download_id}",
        media_id=media_id,
        sequence_id=seq,
        edge_id=edge_between(from_station, "DL1"),
    )
    note_sequence_station(seq, "DL5", local_id)


def instrument_magnet_cache(download_id: int, *, media_id: int | None = None) -> None:
    """Immediate magnet ready (no deferred wait line)."""
    seq = _seq(download_id)
    record_task_run(
        station="ADB2",
        trigger="event",
        outcome="ok",
        subject_ref=f"download:{download_id}",
        media_id=media_id,
        sequence_id=seq,
        edge_id=edge_between("DL1", "ADB2", "attend"),
    )


def instrument_adb2_magnet_wait_touch(download_id: int, *, media_id: int | None = None) -> None:
    from mediatracker.metro.record import touch_adb2_magnet_wait

    touch_adb2_magnet_wait(download_id=download_id, media_id=media_id, sequence_id=_seq(download_id))


def instrument_adb2_magnet_wait_close(download_id: int, *, media_id: int | None = None, success: bool) -> None:
    from mediatracker.metro.record import close_adb2_magnet_wait

    close_adb2_magnet_wait(download_id=download_id, outcome="ok" if success else "error")


def instrument_unlock(
    download_id: int,
    *,
    deferred: bool = False,
    media_id: int | None = None,
    duration_ms: int | None = None,
) -> None:
    seq = _seq(download_id)
    record_task_run(
        station="ADB3",
        trigger="event",
        outcome="deferred" if deferred else "ok",
        subject_ref=f"download:{download_id}",
        media_id=media_id,
        sequence_id=seq,
        edge_id=edge_between("DL1", "ADB3", "attend"),
        duration_ms=duration_ms,
    )


def instrument_import_ready(download_id: int, *, media_id: int | None = None) -> None:
    seq = _seq(download_id)
    record_task_run(
        station="RNG1",
        trigger="event",
        outcome="ok",
        subject_ref=f"download:{download_id}",
        media_id=media_id,
        sequence_id=seq,
        parent_local_id=parent_local_for_trigger(seq, "DL5"),
        edge_id="C10",
    )


def instrument_finalize(download_id: int, *, media_id: int | None = None) -> None:
    seq = _seq(download_id)
    local_id = record_task_run(
        station="RNG5",
        trigger="event",
        outcome="ok",
        subject_ref=f"download:{download_id}",
        media_id=media_id,
        sequence_id=seq,
        parent_local_id=parent_local_for_trigger(seq, "RNG1"),
        edge_id=edge_between("RNG1", "RNG5", "attend"),
    )
    note_sequence_station(seq, "RNG5", local_id)


def instrument_hive_catalog_sync(download_id: int, *, success: bool, media_id: int | None = None) -> None:
    seq = _seq(download_id)
    record_task_run(
        station="RUC2",
        trigger="event",
        outcome="ok" if success else "error",
        subject_ref=f"download:{download_id}",
        media_id=media_id,
        sequence_id=seq,
        parent_local_id=parent_local_for_trigger(seq, "RNG5"),
        edge_id="C16",
    )


def instrument_sui3_stop_follow(
    *,
    db: object | None = None,
    media: object,
    item: object | None = None,
    settings: object | None = None,
    download_id: int | None = None,
) -> None:
    if not is_pass_collection_active():
        return
    media_id = int(media.id)
    rank_cause: str | None = None
    if db is not None and item is not None and settings is not None:
        from mediatracker.metro.profile_rank import rank_cause_for_item

        rank_cause = rank_cause_for_item(db, media, item, settings=settings)
    seq = _seq(download_id) if download_id is not None else None
    record_task_run(
        station="SUI3",
        trigger="event",
        outcome="ok",
        subject_ref=media_subject(media_id),
        media_id=media_id,
        sequence_id=seq,
        parent_local_id=parent_local_for_trigger(seq, "RNG5") if seq else None,
        cause=rank_cause,
        edge_id="C13",
    )


def instrument_sui4_retire_obtained(*, media_id: int) -> None:
    record_task_run(
        station="SUI4",
        trigger="schedule",
        outcome="ok",
        subject_ref=media_subject(media_id),
        media_id=media_id,
        edge_id="C23",
    )


def instrument_sui6_replacement_proposal(
    *,
    db: object,
    media: object,
    settings: object,
    repli_profile_id: int | None = None,
) -> None:
    if not is_pass_collection_active():
        return
    media_id = int(media.id)
    rank_cause: str | None = None
    if repli_profile_id is not None:
        from mediatracker.metro.profile_rank import rank_cause_for_profile_id

        rank_cause = rank_cause_for_profile_id(db, media, repli_profile_id, settings=settings)
    record_task_run(
        station="SUI6",
        trigger="schedule",
        outcome="ok",
        subject_ref=media_subject(media_id),
        media_id=media_id,
        cause=rank_cause,
        edge_id="C18",
    )


def instrument_download_sequence_end(download_id: int) -> None:
    release_download_sequence(download_id)


def instrument_source_apply_link(download_id: int, *, media_id: int | None = None, row: object | None = None) -> None:
    bind_download_sequence(download_id, row)
    seq = _seq(download_id)
    record_task_run(
        station="SRC8",
        trigger="event",
        outcome="ok",
        subject_ref=f"download:{download_id}",
        media_id=media_id,
        sequence_id=seq,
        edge_id=edge_between("SRC8", "DL1", "alimente"),
    )


def instrument_source_poll(*, did_work: bool, media_id: int | None = None, duration_ms: int | None = None) -> None:
    subject = media_subject(media_id) if media_id is not None else None
    if did_work:
        record_task_run(
            station="SRC1",
            trigger="schedule",
            outcome="ok",
            subject_ref=subject,
            media_id=media_id,
            duration_ms=duration_ms,
        )
    else:
        record_pass_idle_change(
            station="SRC1",
            state_key="idle",
            outcome="nothing",
            trigger="schedule",
            dedupe_same_state=False,
        )


def instrument_jellyfin_wait_scan(*, scanning: bool) -> None:
    record_pass_idle_change(
        station="JF4",
        state_key="scanning" if scanning else "idle",
        outcome="deferred" if scanning else "nothing",
        trigger="schedule",
        waiting_on="JF1",
        edge_id=edge_between("JF1", "JF4", "attend"),
        cause="Scan Jellyfin en cours" if scanning else None,
        dedupe_same_state=True,
    )


def instrument_jellyfin_refresh(*, fired: bool, duration_ms: int | None = None) -> None:
    if fired:
        record_task_run(station="JF1", trigger="schedule", outcome="ok", duration_ms=duration_ms)
    else:
        record_pass_idle_change(
            station="JF1",
            state_key="idle",
            outcome="nothing",
            trigger="schedule",
            dedupe_same_state=False,
        )


def instrument_jellyfin_sync(*, polled: bool) -> None:
    if polled:
        record_task_run(station="JF2", trigger="schedule", outcome="ok")
    else:
        record_pass_idle_change(
            station="JF2",
            state_key="idle",
            outcome="nothing",
            trigger="schedule",
            dedupe_same_state=False,
        )


def instrument_library_volume_block(*, blocked: bool) -> None:
    if blocked:
        record_blocked_continuous(
            station="SRC1",
            blocked_by="BIB3:mass_absence",
            cause="Garde volume bibliothèque",
        )
    else:
        clear_blocked("SRC1", "BIB3:mass_absence")


def instrument_sqlite_locked(*, label: str) -> None:
    mark_pass_sqlite_locked()
    record_blocked_continuous(
        station="SOC1",
        blocked_by="SOC7:locked",
        cause=label,
        edge_id="C71",
    )


def instrument_maintenance_block() -> None:
    record_blocked_continuous(
        station="SOC1",
        blocked_by="SOC3:maintenance",
        cause="Porte maintenance active",
    )


def instrument_worker_pass_soc_unblock() -> None:
    if not pass_saw_sqlite_locked():
        clear_blocked("SOC1", "SOC7:locked")
    clear_blocked("SOC1", "SOC3:maintenance")
