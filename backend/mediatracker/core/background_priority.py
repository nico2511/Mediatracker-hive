"""Best-effort low CPU and I/O priority for heavy library work.

``os.nice`` affects the whole process and is only used from the worker service.
I/O priority is lowered for the current thread so API-triggered scans and
verify-all do not starve request handling.
"""

from __future__ import annotations

import ctypes
import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# Idle I/O class, lowest within class.
_IOPRIO_CLASS_IDLE = 3
_IOPRIO_PRIO_LEVEL = 7
_IOPRIO_WHO_PROCESS = 1
# Linux: who=0 with IOPRIO_WHO_PROCESS targets the calling thread.
_IOPRIO_WHO_THREAD = 0
_NICE_DELTA = 10
_cpu_lowered = False


def _ioprio_value() -> int:
    return (_IOPRIO_CLASS_IDLE << 13) | _IOPRIO_PRIO_LEVEL


def _syscall_ioprio_set(who: int, ioprio: int) -> bool:
    if not sys.platform.startswith("linux"):
        return False
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
    except OSError:
        return False
    syscall = getattr(libc, "syscall", None)
    if syscall is None:
        return False
    # SYS_ioprio_set on common Linux arches.
    for nr in (251,):
        try:
            if syscall(nr, _IOPRIO_WHO_PROCESS, who, ioprio) == 0:
                return True
        except OSError:
            continue
    return False


def lower_io_priority(*, allow_ionice_cli: bool = False) -> None:
    """Lower I/O priority for this thread. Never raises."""
    value = _ioprio_value()
    if _syscall_ioprio_set(_IOPRIO_WHO_THREAD, value):
        return
    if not allow_ionice_cli or not shutil.which("ionice"):
        return
    try:
        subprocess.run(
            ["ionice", "-c3", "-n7", "-p", str(os.getpid())],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


def lower_cpu_priority() -> None:
    """Raise nice value for this process once. Never raises."""
    global _cpu_lowered
    if _cpu_lowered:
        return
    try:
        os.nice(_NICE_DELTA)
        _cpu_lowered = True
    except (AttributeError, OSError):
        pass


def reset_cpu_priority_state_for_tests() -> None:
    """Allow tests to exercise first-call behavior."""
    global _cpu_lowered
    _cpu_lowered = False


def apply_background_priority(*, process_wide: bool = False) -> None:
    lower_io_priority(allow_ionice_cli=process_wide)
    if process_wide:
        lower_cpu_priority()


@contextmanager
def background_priority(*, process_wide: bool = False) -> Iterator[None]:
    apply_background_priority(process_wide=process_wide)
    yield
