from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
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


def test_verification_run_requires_an_explicit_issue_number() -> None:
    result = subprocess.run(
        ["make", "verification-run", "VERIFY_ISSUE="],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "VERIFY_ISSUE is required" in result.stderr


def test_verification_run_passes_the_explicit_issue_to_the_runner() -> None:
    result = subprocess.run(
        [
            "make",
            "-n",
            "verification-run",
            "VERIFY_ISSUE=197",
            "VERIFY_WORKTREE=issue-197-fail-closed-issue",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert '--issue "197"' in result.stdout
    assert '--worktree "issue-197-fail-closed-issue"' in result.stdout


def test_playwright_targets_keep_profiles_and_use_the_bounded_faulthandler_timeout() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    excluded_markers = (
        " and not remote_readonly and not remote_mutation and not live_email and not live_provider"
    )
    expected = {
        "test-playwright-smoke": f"-m 'smoke and not quarantine{excluded_markers}'",
        "test-playwright-core": f"-m 'core and not quarantine{excluded_markers}'",
        "test-playwright": f"-m '(smoke or core or full) and not quarantine{excluded_markers}'",
        "test-accessibility": f"-m 'accessibility{excluded_markers}'",
    }
    for target, marker_expression in expected.items():
        target_body = makefile.split(f"{target}:\n", 1)[1].split("\n\n", 1)[0]
        assert "-o faulthandler_timeout=120" in target_body
        assert marker_expression in target_body
        assert "--reruns" not in target_body


def test_synthetic_hanging_test_fails_with_a_named_timeout() -> None:
    probe_root = ROOT / ".tmp"
    probe_root.mkdir(parents=True, exist_ok=True)
    probe_directory = Path(tempfile.mkdtemp(prefix="pytest-faulthandler-contract-", dir=probe_root))
    probe = probe_directory / "test_hanging.py"
    probe.write_text(
        "import time\n\n\ndef test_synthetic_wedged_browser() -> None:\n    time.sleep(30)\n",
        encoding="utf-8",
    )
    started = time.monotonic()
    try:
        result = subprocess.run(
            [
                "timeout",
                "--signal=TERM",
                "--kill-after=2s",
                "8s",
                "uv",
                "run",
                "--frozen",
                "pytest",
                str(probe),
                "-o",
                "faulthandler_timeout=1",
                "-v",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    finally:
        shutil.rmtree(probe_directory, ignore_errors=True)

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert time.monotonic() - started < 15
    assert "test_synthetic_wedged_browser" in output
    assert "Timeout" in output
