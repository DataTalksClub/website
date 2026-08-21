from __future__ import annotations

import json
import subprocess
from copy import deepcopy
from datetime import UTC, datetime

import pytest

from ci.runner import RunnerError, command_for, run_plan
from ci.verification import build_plan
from tests_ci.helpers import repository_with_change, selection_for


def plan_for(tmp_path, changed):
    repository, base, head = repository_with_change(tmp_path, changed)
    selection, records = selection_for(tuple(changed), base=base, head=head)
    return build_plan(
        repository=repository,
        repository_id="DataTalksClub/website",
        base=base,
        head=head,
        selection=selection,
        records=records,
        now=datetime(2026, 8, 9, 12, tzinfo=UTC),
    )


def test_runner_uses_only_allowlisted_argument_vectors(tmp_path) -> None:
    focused = plan_for(tmp_path, {"api/service.py": "changed\n"})
    assert command_for(focused, "django") == ("make", "test-ci-focused")
    assert command_for(focused, "playwright") == ("make", "test-playwright-core")
    assert command_for(focused, "content_invariants") == (
        "make",
        "verification-content-invariants",
    )


def test_runner_uses_complete_suites_for_full_plan(tmp_path) -> None:
    full = plan_for(tmp_path, {"api/templates/api/page.html": "changed\n"})
    assert command_for(full, "django") == ("make", "test-django-full")
    assert command_for(full, "playwright") == ("make", "test-playwright")


def test_runner_rejects_human_or_unknown_components(tmp_path) -> None:
    plan = plan_for(tmp_path, {"api/service.py": "changed\n"})
    with pytest.raises(RunnerError):
        command_for(plan, "screenshots")
    malformed = deepcopy(plan)
    malformed["test_labels"] = []
    with pytest.raises(RunnerError):
        command_for(malformed, "django")


def test_runner_rejects_a_different_actual_runner_before_component_execution(
    monkeypatch, tmp_path
) -> None:
    plan = plan_for(tmp_path, {"api/service.py": "changed\n"})
    monkeypatch.setenv("ImageOS", "ubuntu24")
    monkeypatch.setenv("ImageVersion", "20260899.1")

    with pytest.raises(RunnerError, match="execution environment"):
        run_plan(
            plan=plan,
            repository=tmp_path / "repository",
            output_directory=tmp_path / "evidence",
            issue=113,
            worktree="qa",
            producer_role="tester",
        )


def test_runner_records_the_selected_tester_role(monkeypatch, tmp_path) -> None:
    plan = plan_for(tmp_path, {"api/service.py": "changed\n"})

    def completed(command, **_kwargs):
        if command == ["uv", "--version"]:
            return subprocess.CompletedProcess(command, 0, stdout="uv 0.10.11\n")
        if command[:3] == ["git", "-C", str(tmp_path / "repository")]:
            return subprocess.CompletedProcess(command, 0, stdout=f"{plan['head']}\n")
        output = "2 passed in 0.01s\n"
        if command[:2] in (["make", "test-playwright-core"], ["make", "test-playwright"]):
            output += (
                "DTC_FLAKE_POLICY_V1 attempted=2 passed=2 failed=0 skipped=0 "
                "rerun=0 quarantined=0 complete=1\n"
            )
        return subprocess.CompletedProcess(command, 0, stdout=output)

    class CompletedPopen:
        def __init__(self, command, **kwargs):
            self.args = tuple(command)
            self.pid = 424_242
            self.returncode = None
            if tuple(command) == ("make", "verification-container"):
                with open(
                    kwargs["env"]["VERIFY_CONTAINER_OUTPUT"], "w", encoding="utf-8"
                ) as stream:
                    json.dump(
                        {
                            "assertions": ["container_contract"],
                            "revision": plan["head"],
                            "schema_version": 1,
                            "status": "pass",
                        },
                        stream,
                    )
            elif kwargs.get("stdout") is not None:
                output = "2 passed in 0.01s\n"
                if self.args in {
                    ("make", "test-playwright-core"),
                    ("make", "test-playwright"),
                }:
                    output += (
                        "DTC_FLAKE_POLICY_V1 attempted=2 passed=2 failed=0 skipped=0 "
                        "rerun=0 quarantined=0 complete=1\n"
                    )
                kwargs["stdout"].write(output)

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            self.returncode = 0
            return 0

    monkeypatch.setattr("ci.runner.subprocess.run", completed)
    monkeypatch.setattr("ci.runner.subprocess.Popen", CompletedPopen)
    output = tmp_path / "evidence"
    assert (
        run_plan(
            plan=plan,
            repository=tmp_path / "repository",
            output_directory=output,
            issue=113,
            worktree="qa",
            producer_role="tester",
        )
        == 0
    )
    envelopes = [
        json.loads(path.read_text(encoding="utf-8")) for path in output.glob("*-evidence.json")
    ]
    assert envelopes
    assert {item["origin"]["producer_role"] for item in envelopes} == {"tester"}
