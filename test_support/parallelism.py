"""Memory-aware worker count for ``manage.py test --parallel``.

Django turns a bare ``--parallel`` (``auto``) into one worker per CPU core
unless ``DJANGO_TEST_PROCESSES`` says otherwise.  On a 12-core host every
worker is a full Django process of roughly 250 MiB, so an agent session
running the suite inside a 1.5 GiB memory cgroup forked twelve of them and
the kernel OOM-killed the session (aplexer session 22ae36a9, 2026-10-31).

The test settings therefore default ``DJANGO_TEST_PROCESSES`` to the number
of workers the available memory can hold: the tightest cgroup-v2
``memory.max`` on the way to the root minus what that cgroup already uses,
or ``MemAvailable`` when no cgroup limit applies.  An explicit
``DJANGO_TEST_PROCESSES`` or ``--parallel N`` still wins.
"""

from __future__ import annotations

import os
from pathlib import Path

PROCESSES_ENV = "DJANGO_TEST_PROCESSES"
# Observed resident size of one Django test worker is ~250 MiB; the budget
# leaves room for per-test peaks.
WORKER_BUDGET_BYTES = 400 * 1024 * 1024
# Kept free for the coordinating test process and whatever else shares the
# limit (the agent that launched the run, for one).
RESERVE_BYTES = 512 * 1024 * 1024


def _read_int(path: Path) -> int | None:
    try:
        text = path.read_text().strip()
    except OSError:
        return None
    if not text or text == "max":
        return None
    try:
        return int(text)
    except ValueError:
        return None


def cgroup_available_bytes(
    cgroup_root: Path = Path("/sys/fs/cgroup"),
    proc_self_cgroup: Path = Path("/proc/self/cgroup"),
) -> int | None:
    """Headroom under the tightest cgroup-v2 memory limit above this process."""

    try:
        lines = proc_self_cgroup.read_text().splitlines()
    except OSError:
        return None
    relative = next((line[3:] for line in lines if line.startswith("0::")), None)
    if relative is None:
        return None
    directory = cgroup_root / relative.lstrip("/")
    tightest: int | None = None
    while True:
        limit = _read_int(directory / "memory.max")
        if limit is not None:
            used = _read_int(directory / "memory.current") or 0
            headroom = max(limit - used, 0)
            tightest = headroom if tightest is None else min(tightest, headroom)
        if directory == cgroup_root or cgroup_root not in directory.parents:
            return tightest
        directory = directory.parent


def meminfo_available_bytes(meminfo: Path = Path("/proc/meminfo")) -> int | None:
    try:
        lines = meminfo.read_text().splitlines()
    except OSError:
        return None
    for line in lines:
        if line.startswith("MemAvailable:"):
            parts = line.split()
            try:
                return int(parts[1]) * 1024
            except (IndexError, ValueError):
                return None
    return None


def memory_capped_processes(cpu_count: int, available_bytes: int | None) -> int:
    """Workers to run: one per core, but never more than memory can hold."""

    cpus = max(cpu_count, 1)
    if available_bytes is None:
        return cpus
    by_memory = (available_bytes - RESERVE_BYTES) // WORKER_BUDGET_BYTES
    return max(1, min(cpus, by_memory))


def default_test_processes() -> int:
    cgroup = cgroup_available_bytes()
    host = meminfo_available_bytes()
    known = [value for value in (cgroup, host) if value is not None]
    return memory_capped_processes(os.cpu_count() or 1, min(known) if known else None)


def apply_default_test_processes() -> None:
    """Default ``DJANGO_TEST_PROCESSES`` without overriding an explicit value."""

    if PROCESSES_ENV not in os.environ:
        os.environ[PROCESSES_ENV] = str(default_test_processes())
