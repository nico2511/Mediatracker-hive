"""Library scans and classify run with lowered priority."""

import os
from unittest.mock import patch

from mediatracker.core import background_priority as bp
from mediatracker.core.metrics.worker_counters import TASK_CLASSIFY, TASK_LIBRARY_SCAN, track_task


def test_track_task_lowers_worker_priority():
    calls: list[bool] = []

    def _fake(*, process_wide: bool = False) -> None:
        calls.append(process_wide)

    with patch("mediatracker.core.background_priority.apply_background_priority", side_effect=_fake):
        with track_task(TASK_LIBRARY_SCAN):
            pass
        with track_task(TASK_CLASSIFY):
            pass

    assert calls == [True, True]


def test_verify_all_lowers_io_without_process_nice():
    from mediatracker.core.metrics.worker_counters import TASK_VERIFY_ALL

    calls: list[bool] = []

    def _fake(*, process_wide: bool = False) -> None:
        calls.append(process_wide)

    with patch("mediatracker.core.background_priority.apply_background_priority", side_effect=_fake):
        with track_task(TASK_VERIFY_ALL):
            pass

    assert calls == [False]


def test_lower_cpu_priority_only_once_per_process():
    bp.reset_cpu_priority_state_for_tests()
    deltas: list[int] = []

    def fake_nice(delta: int) -> int:
        deltas.append(delta)
        return 0

    with patch.object(os, "nice", side_effect=fake_nice):
        bp.lower_cpu_priority()
        bp.lower_cpu_priority()
        bp.apply_background_priority(process_wide=True)
    assert deltas == [bp._NICE_DELTA]


def test_ionice_cli_targets_current_pid(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(argv, **_kwargs):
        calls.append(list(argv))

    monkeypatch.setattr(bp, "_syscall_ioprio_set", lambda *_a, **_k: False)
    monkeypatch.setattr(bp.shutil, "which", lambda _name: "/usr/bin/ionice")
    monkeypatch.setattr(bp.subprocess, "run", fake_run)
    bp.lower_io_priority(allow_ionice_cli=True)
    assert calls == [["ionice", "-c3", "-n7", "-p", str(os.getpid())]]
