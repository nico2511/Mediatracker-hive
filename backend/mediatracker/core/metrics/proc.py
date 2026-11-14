"""Linux /proc readers for lightweight process metrics. Missing data yields nulls."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_PAGE_SIZE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096


@dataclass(frozen=True)
class ProcTimes:
    utime: int
    stime: int


@dataclass(frozen=True)
class ProcessMetrics:
    pid: int | None = None
    cpu_percent: float | None = None
    rss_bytes: int | None = None
    write_bytes: int | None = None


def proc_root(root: Path | None = None) -> Path:
    return root if root is not None else Path("/proc")


def read_process_times(proc_root: Path, pid: int) -> ProcTimes | None:
    try:
        raw = (proc_root / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    close = raw.rfind(")")
    if close < 0:
        return None
    tail = raw[close + 2 :].split()
    if len(tail) < 14:
        return None
    try:
        return ProcTimes(utime=int(tail[11]), stime=int(tail[12]))
    except ValueError:
        return None


def read_rss_bytes(proc_root: Path, pid: int) -> int | None:
    try:
        raw = (proc_root / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    close = raw.rfind(")")
    if close < 0:
        return None
    tail = raw[close + 2 :].split()
    if len(tail) < 22:
        return None
    try:
        return int(tail[21]) * _PAGE_SIZE
    except ValueError:
        return None


def read_write_bytes(proc_root: Path, pid: int) -> int | None:
    try:
        text = (proc_root / str(pid) / "io").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("write_bytes:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
    return None


def read_system_jiffies(proc_root: Path) -> int | None:
    try:
        first = (proc_root / "stat").read_text(encoding="utf-8", errors="replace").splitlines()[0]
    except (OSError, IndexError):
        return None
    parts = first.split()
    if len(parts) < 8 or parts[0] != "cpu":
        return None
    try:
        return sum(int(parts[i]) for i in range(1, 8))
    except ValueError:
        return None


def cpu_percent_between(
    prev_times: ProcTimes,
    prev_jiffies: int,
    cur_times: ProcTimes,
    cur_jiffies: int,
    *,
    cpu_count: int,
) -> float | None:
    proc_delta = (cur_times.utime - prev_times.utime) + (cur_times.stime - prev_times.stime)
    sys_delta = cur_jiffies - prev_jiffies
    if proc_delta < 0 or sys_delta <= 0 or cpu_count <= 0:
        return None
    return round(100.0 * proc_delta / (sys_delta * cpu_count), 2)


def sample_process(
    proc_root: Path,
    pid: int | None,
    *,
    prev_times: ProcTimes | None,
    prev_jiffies: int | None,
    cur_jiffies: int | None,
    cpu_count: int,
) -> tuple[ProcessMetrics, ProcTimes | None]:
    if pid is None:
        return ProcessMetrics(), None
    times = read_process_times(proc_root, pid)
    rss = read_rss_bytes(proc_root, pid)
    write_bytes = read_write_bytes(proc_root, pid)
    cpu: float | None = None
    if times is not None and prev_times is not None and prev_jiffies is not None and cur_jiffies is not None:
        cpu = cpu_percent_between(prev_times, prev_jiffies, times, cur_jiffies, cpu_count=cpu_count)
    return (
        ProcessMetrics(pid=pid, cpu_percent=cpu, rss_bytes=rss, write_bytes=write_bytes),
        times,
    )


def read_cmdline(proc_root: Path, pid: int) -> str:
    try:
        raw = (proc_root / str(pid) / "cmdline").read_bytes()
    except OSError:
        return ""
    return raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()


def find_pid_by_cmdline(proc_root: Path, needle: str) -> int | None:
    needle_l = needle.lower()
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return None
    for entry in entries:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        cmd = read_cmdline(proc_root, pid).lower()
        if needle_l in cmd:
            return pid
    return None


def resolve_role_pids(proc_root: Path, api_pid: int) -> dict[str, int | None]:
    return {
        "api": api_pid,
        "worker": find_pid_by_cmdline(proc_root, "mediatracker.worker"),
        "aria2": find_pid_by_cmdline(proc_root, "aria2c"),
    }
