"""A bare ``--parallel`` must not fork more Django workers than memory holds."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from test_support import parallelism
from test_support.parallelism import (
    PROCESSES_ENV,
    RESERVE_BYTES,
    WORKER_BUDGET_BYTES,
    apply_default_test_processes,
    cgroup_available_bytes,
    meminfo_available_bytes,
    memory_capped_processes,
)

MIB = 1024 * 1024


def _cgroup_tree(tmp_path: Path, limits: dict[str, tuple[str, str]]) -> tuple[Path, Path]:
    root = tmp_path / "cgroup"
    for relative, (maximum, current) in limits.items():
        directory = root / relative if relative else root
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "memory.max").write_text(f"{maximum}\n")
        (directory / "memory.current").write_text(f"{current}\n")
    proc = tmp_path / "proc-self-cgroup"
    deepest = max(limits, key=len)
    proc.write_text(f"0::/{deepest}\n")
    return root, proc


def test_oomed_session_shape_gets_one_or_two_workers_not_twelve(tmp_path: Path) -> None:
    # The 2026-10-31 OOM: a 1.5 GiB aplexer scope already holding the
    # ~450 MiB agent, on a 12-core host.
    root, proc = _cgroup_tree(
        tmp_path,
        {
            "": ("max", "0"),
            "user.slice": ("max", str(40_000 * MIB)),
            "user.slice/app.slice/aplexer-workload.scope": (
                str(1500 * MIB),
                str(450 * MIB),
            ),
        },
    )
    available = cgroup_available_bytes(cgroup_root=root, proc_self_cgroup=proc)
    assert available == 1050 * MIB
    # (1050 - 512) MiB of headroom holds one 400 MiB worker.
    assert memory_capped_processes(12, available) == 1
    assert memory_capped_processes(12, RESERVE_BYTES + 3 * WORKER_BUDGET_BYTES) == 3


def test_tightest_ancestor_limit_wins(tmp_path: Path) -> None:
    root, proc = _cgroup_tree(
        tmp_path,
        {
            "parent": (str(2000 * MIB), str(1900 * MIB)),
            "parent/child": (str(8000 * MIB), str(10 * MIB)),
        },
    )
    assert cgroup_available_bytes(cgroup_root=root, proc_self_cgroup=proc) == 100 * MIB


def test_unlimited_cgroup_reports_no_limit(tmp_path: Path) -> None:
    root, proc = _cgroup_tree(tmp_path, {"user.slice/session.scope": ("max", "123")})
    assert cgroup_available_bytes(cgroup_root=root, proc_self_cgroup=proc) is None


def test_cpu_count_stays_the_ceiling_and_one_is_the_floor() -> None:
    assert memory_capped_processes(12, None) == 12
    assert memory_capped_processes(4, 64_000 * MIB) == 4
    assert memory_capped_processes(12, 0) == 1


def test_meminfo_parsing(tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 100 kB\nMemAvailable:    2048 kB\n")
    assert meminfo_available_bytes(meminfo) == 2 * MIB


def test_explicit_environment_value_is_never_overridden() -> None:
    with patch.dict(os.environ, {PROCESSES_ENV: "7"}):
        apply_default_test_processes()
        assert os.environ[PROCESSES_ENV] == "7"
    environment = {k: v for k, v in os.environ.items() if k != PROCESSES_ENV}
    with (
        patch.dict(os.environ, environment, clear=True),
        patch.object(parallelism, "default_test_processes", return_value=3),
    ):
        apply_default_test_processes()
        assert os.environ[PROCESSES_ENV] == "3"
