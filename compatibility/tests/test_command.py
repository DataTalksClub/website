from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from compatibility.report import ParityStatus, loads_report

ROOT = Path(__file__).resolve().parents[2]


def test_checked_real_inputs_write_blocked_report_and_exit_nonzero() -> None:
    output = ROOT / ".tmp" / "tests" / f"checked-real-{uuid.uuid4().hex}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with pytest.raises(CommandError, match="compatibility gate blocked"):
            call_command(
                "compatibility_gate",
                route_sha256="0" * 64,
                asset_sha256="1" * 64,
                projection_sha256="2" * 64,
                output=output,
            )

        report = loads_report(output.read_text(encoding="utf-8"))
        assert report.status is ParityStatus.BLOCKED
        assert report.complete is False
        assert report.expectation_count == 0
        assert [finding.code for finding in report.findings] == ["approved_expectations_missing"]
    finally:
        output.unlink(missing_ok=True)


def test_programmatic_command_rejects_output_outside_project_scratch() -> None:
    output = ROOT / "compatibility" / "outside-report.json"

    with pytest.raises(CommandError, match="below project .tmp"):
        call_command(
            "compatibility_gate",
            route_sha256="0" * 64,
            asset_sha256="1" * 64,
            projection_sha256="2" * 64,
            output=output,
        )

    assert not output.exists()
