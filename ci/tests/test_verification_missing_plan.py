"""Rerun after a cancelled attempt must not traceback on a missing plan.

Run 40155406201 attempt #2 reached ci-gate with no verification plan because
resolve-release never sealed a source identity. ``load_plan`` must raise a
clear ``VerificationError`` naming resolve-release, and the ``report`` CLI
must exit with a ``::error::`` line instead of a FileNotFoundError traceback.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from ci.verification import VerificationError, load_plan


def test_load_plan_missing_reports_resolve_release_without_traceback(tmp_path: Path) -> None:
    missing = tmp_path / "verification-plan.json"
    with pytest.raises(VerificationError) as excinfo:
        load_plan(missing)
    message = str(excinfo.value)
    assert "resolve-release did not produce a plan" in message
    assert str(missing) in message


def test_load_plan_malformed_json_reports_clear_error(tmp_path: Path) -> None:
    bad = tmp_path / "verification-plan.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(VerificationError) as excinfo:
        load_plan(bad)
    assert "not valid JSON" in str(excinfo.value)


def test_report_cli_missing_plan_emits_error_not_traceback(tmp_path: Path) -> None:
    missing = tmp_path / "verification-plan.json"
    output = tmp_path / "verification-report.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "ci.verification",
            "report",
            "--plan",
            str(missing),
            "--phase",
            "ci",
            "--no-fail-exit",
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert completed.returncode == 1
    assert "::error::" in completed.stderr
    assert "resolve-release did not produce a plan" in completed.stderr
    assert "Traceback" not in completed.stderr
