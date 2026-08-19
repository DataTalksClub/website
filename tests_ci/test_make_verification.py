from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_documented_make_variables_drive_tester_final_validation() -> None:
    result = subprocess.run(
        [
            "make",
            "-n",
            "verification-report-check",
            "VERIFY_PLAN=.tmp/custom-plan.json",
            "VERIFY_REPORT=.tmp/custom-report.json",
            "VERIFY_EVIDENCE_DIR=.tmp/custom-evidence",
            "VERIFY_CONSUMER=tester",
            "VERIFY_PHASE=tester",
            "VERIFY_PRODUCER_ROLE=tester",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert '--plan ".tmp/custom-plan.json"' in result.stdout
    assert '--report ".tmp/custom-report.json"' in result.stdout
    assert '--directory ".tmp/custom-evidence"' in result.stdout
    assert '--consumer "tester"' in result.stdout
    assert '--phase "tester"' in result.stdout
    assert 'if test "tester" = engineer' in result.stdout


def test_verification_run_reaches_report_after_runner_failure() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert "runner_status=0" in makefile
    assert "report_status=0" in makefile
    assert "$(MAKE) verification-report-check || report_status=$$?" in makefile
    assert 'if test "$$report_status" -ne 0' in makefile
    assert 'exit "$$runner_status"' in makefile


def test_full_verification_uses_the_owned_sqlite_test_runtime() -> None:
    result = subprocess.run(
        ["make", "-n", "verification-full"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "DTC_SQLITE_PATH" not in result.stdout
    assert "DTC_TEST_RUN_ID" in result.stdout
    assert "DJANGO_SETTINGS_MODULE=website.settings.test" in result.stdout
    assert "uv run --frozen python manage.py migrate --noinput" in result.stdout
