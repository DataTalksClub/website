"""Local verification origins must fail closed without an explicit issue (issue #197).

A local evidence envelope records ``origin.issue``. Before this issue every
entry point silently substituted the hard-coded number 113 when the caller
supplied none, mis-attributing the audit trail to a long-closed issue. These
tests drive the real CLIs (and the Makefile surface) so each local-origin
entry point now refuses to record without a named issue, while the
``github_actions`` origin — which never records an issue field — keeps
validating unchanged.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from ci.verification import build_plan, dump_json
from tests_ci.helpers import repository_with_change, selection_for

ROOT = Path(__file__).resolve().parents[1]


def selector_record_arguments(tmp_path: Path) -> list[str]:
    """Build the arguments of a fully valid ``record`` invocation for ``selector``.

    The fixture is deliberately complete: the sibling tests prove that the
    identical command succeeds once ``--issue`` is supplied (or, for the
    ``github_actions`` origin, is legitimately absent), so a failure can only
    come from the missing issue number.
    """

    repository, base, head = repository_with_change(tmp_path, {"api/service.py": "changed\n"})
    selection, records = selection_for(("api/service.py",), base=base, head=head)
    plan = build_plan(
        repository=repository,
        repository_id="DataTalksClub/website",
        base=base,
        head=head,
        selection=selection,
        records=records,
        now=datetime(2026, 8, 18, 12, tzinfo=UTC),
    )
    work = tmp_path / "record"
    work.mkdir()
    plan_path = work / "verification-plan.json"
    dump_json(plan, plan_path)
    machine_output = work / "selector-machine-output.json"
    dump_json(plan, machine_output)
    environment = work / "selector-environment.json"
    dump_json(plan["components"]["selector"]["environment"], environment)
    evidence = tmp_path / "evidence" / "selector-evidence.json"
    return [
        "--plan",
        str(plan_path),
        "--component",
        "selector",
        "--result",
        "success",
        "--command",
        plan["components"]["selector"]["command"],
        "--output",
        str(evidence),
        "--artifact-root",
        str(tmp_path),
        "--machine-output",
        str(machine_output),
        "--execution-environment",
        str(environment),
    ]


def run_module(module: str, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", module, *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_record_fails_closed_for_local_origins_without_an_issue(tmp_path: Path) -> None:
    result = run_module(
        "ci.verification",
        [
            "record",
            *selector_record_arguments(tmp_path),
            "--origin-kind",
            "local",
            "--producer-role",
            "engineer",
            "--worktree",
            "issue-197-fail-closed-issue",
        ],
    )
    assert result.returncode != 0
    assert "--issue" in result.stderr
    assert not (tmp_path / "evidence").exists()
    assert not list(tmp_path.rglob("*-result.json"))


def test_record_attributes_local_evidence_to_the_explicit_issue(tmp_path: Path) -> None:
    result = run_module(
        "ci.verification",
        [
            "record",
            *selector_record_arguments(tmp_path),
            "--origin-kind",
            "local",
            "--issue",
            "197",
            "--producer-role",
            "engineer",
            "--worktree",
            "issue-197-fail-closed-issue",
        ],
    )
    assert result.returncode == 0, result.stderr
    envelope = json.loads(
        (tmp_path / "evidence" / "selector-evidence.json").read_text(encoding="utf-8")
    )
    assert envelope["origin"] == {
        "issue": 197,
        "kind": "local",
        "producer_role": "engineer",
        "worktree": "issue-197-fail-closed-issue",
    }


def test_record_still_validates_github_actions_origins_without_an_issue(
    tmp_path: Path,
) -> None:
    result = run_module(
        "ci.verification",
        [
            "record",
            *selector_record_arguments(tmp_path),
            "--origin-kind",
            "github_actions",
            # The workflow's record calls pass no --issue and always identify
            # the job and artifact; only the issue input must stay optional.
            "--job-id",
            "classification",
            "--artifact-id",
            "ci-selection-1-attempt-1",
        ],
    )
    assert result.returncode == 0, result.stderr
    envelope = json.loads(
        (tmp_path / "evidence" / "selector-evidence.json").read_text(encoding="utf-8")
    )
    assert envelope["origin"]["kind"] == "github_actions"
    assert "issue" not in envelope["origin"]


def test_runner_fails_closed_without_an_issue(tmp_path: Path) -> None:
    result = run_module(
        "ci.runner",
        [
            "--plan",
            str(tmp_path / "verification-plan.json"),
            "--repository",
            str(tmp_path),
            "--output-directory",
            str(tmp_path / "evidence"),
            "--worktree",
            "issue-197-fail-closed-issue",
            "--producer-role",
            "engineer",
        ],
    )
    assert result.returncode != 0
    assert "--issue" in result.stderr
    assert not (tmp_path / "evidence").exists()


def test_no_silent_default_issue_number_remains_in_the_local_tools() -> None:
    assert "VERIFY_ISSUE ?= 113" not in (ROOT / "Makefile").read_text(encoding="utf-8")
    for module in ("ci/verification.py", "ci/runner.py"):
        assert "default=113" not in (ROOT / module).read_text(encoding="utf-8")
