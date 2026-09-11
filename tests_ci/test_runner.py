from __future__ import annotations

import json
import subprocess
from copy import deepcopy
from datetime import UTC, datetime

import pytest

from ci.runner import ComponentExecution, RunnerError, command_for, run_plan
from ci.verification import build_plan, create_report
from tests_ci.helpers import repository_with_change, selection_for

CI_SCRIPT = ("uv", "run", "--frozen", "python", "scripts/ci.py")


@pytest.fixture(autouse=True)
def local_synthetic_execution(monkeypatch):
    """Bind every plan in this module to the host that executes it.

    These are local synthetic execution scenarios: ``run_plan`` runs every
    component on this machine, never on the container job's remote runner.  A
    shell that exports the real workflow's target declarations (or the ci.yml
    job environment itself, which sets them workflow-wide) would otherwise make
    ``build_plan`` authorize the remote aarch64 container runner and the local
    execution would then fail its own environment comparison.  Deleting the two
    declarations with monkeypatch restores the ambient environment after every
    test; the declared-target acceptance and rejection contracts stay in
    ``tests_ci/test_verification.py`` and are not weakened here.
    """

    monkeypatch.delenv("VERIFICATION_CONTAINER_ARCHITECTURE", raising=False)
    monkeypatch.delenv("VERIFICATION_CONTAINER_RUNNER_IMAGE", raising=False)


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
    assert command_for(focused, "django") == (*CI_SCRIPT, "test-ci-focused")
    assert command_for(focused, "playwright") == (*CI_SCRIPT, "test-playwright-smoke")


def test_runner_uses_complete_suites_for_full_plan(tmp_path) -> None:
    full = plan_for(tmp_path, {"api/templates/api/page.html": "changed\n"})
    assert command_for(full, "django") == (*CI_SCRIPT, "test-django-full")
    assert command_for(full, "playwright") == (*CI_SCRIPT, "test-playwright")


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
    # The synthetic plan must stay bound to the host that runs its components.
    # If this fixture ever stops isolating the workflow's remote container
    # target, the container component would authorize a foreign machine/image
    # while the execution below still happens here; name that drift directly
    # instead of leaving only the runner's opaque environment rejection.
    assert (
        plan["components"]["container"]["environment"]["architecture"]
        == (plan["environment"]["architecture"])
    )
    assert (
        plan["components"]["container"]["environment"]["runner_image"]
        == (plan["environment"]["runner_image"])
    )

    def completed(command, **_kwargs):
        if command == ["uv", "--version"]:
            return subprocess.CompletedProcess(command, 0, stdout="uv 0.10.11\n")
        if command[:3] == ["git", "-C", str(tmp_path / "repository")]:
            if command[3:5] == ["status", "--porcelain"]:
                # The fake source-binding check sees a clean checkout.
                return subprocess.CompletedProcess(command, 0, stdout="")
            return subprocess.CompletedProcess(command, 0, stdout=f"{plan['head']}\n")
        output = "2 passed in 0.01s\n"
        if tuple(command) in (
            (*CI_SCRIPT, "test-playwright-smoke"),
            (*CI_SCRIPT, "test-playwright-core"),
            (*CI_SCRIPT, "test-playwright"),
        ):
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
            if tuple(command) == (*CI_SCRIPT, "verification-container"):
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
                    (*CI_SCRIPT, "test-playwright-smoke"),
                    (*CI_SCRIPT, "test-playwright-core"),
                    (*CI_SCRIPT, "test-playwright"),
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


def commit_plan_for(tmp_path, changed):
    """A commit-mode plan over a real synthetic repository, with its checkout."""

    repository, base, head = repository_with_change(tmp_path, changed)
    selection, records = selection_for(tuple(changed), base=base, head=head)
    return repository, build_plan(
        repository=repository,
        repository_id="DataTalksClub/website",
        base=base,
        head=head,
        selection=selection,
        records=records,
        now=datetime(2026, 8, 9, 12, tzinfo=UTC),
    )


def worktree_plan_for(tmp_path, changed):
    """A worktree-mode plan over a real synthetic repository."""

    repository, base, head = repository_with_change(tmp_path, changed)
    selection, records = selection_for(tuple(changed), base=base, head=head)
    return repository, build_plan(
        repository=repository,
        repository_id="DataTalksClub/website",
        base=base,
        head=head,
        selection=selection,
        records=records,
        include_worktree=True,
        now=datetime(2026, 8, 9, 12, tzinfo=UTC),
    )


def _synthetic_run(plan, repository, output):
    return run_plan(
        plan=plan,
        repository=repository,
        output_directory=output,
        issue=None,
        worktree="synthetic-ci02",
        producer_role="engineer",
    )


def test_runner_refuses_dirty_source_before_execution(tmp_path) -> None:
    # CI-02, commit-mode probe: a tracked file edited after the plan was cut
    # leaves HEAD on the planned commit, but the checkout is not that commit;
    # the run must refuse before any component executes.
    repository, plan = commit_plan_for(tmp_path, {"README.md": "changed\n"})
    (repository / "README.md").write_text("dirty before execution\n", encoding="utf-8")
    output = tmp_path / "evidence"

    assert _synthetic_run(plan, repository, output) == 1

    diagnostic = json.loads((output / "source-drift.json").read_text(encoding="utf-8"))
    assert diagnostic["reason"] == "source_changed_during_verification"
    assert not list(output.glob("*-evidence.json"))
    assert not (output / "selector-plan.json").exists()


def test_runner_refuses_a_moved_head(tmp_path) -> None:
    repository, plan = commit_plan_for(tmp_path, {"README.md": "changed\n"})
    git = ("git", "-C", str(repository))
    subprocess.run(
        (*git, "commit", "--allow-empty", "-m", "moved"),
        check=True,
        capture_output=True,
    )
    output = tmp_path / "evidence"

    assert _synthetic_run(plan, repository, output) == 1

    diagnostic = json.loads((output / "source-drift.json").read_text(encoding="utf-8"))
    assert "source_changed_during_verification" in diagnostic["detail"]


def test_runner_stops_and_invalidates_when_source_drifts_during_execution(
    monkeypatch, tmp_path
) -> None:
    # CI-02, worktree-mode probe: the source changes inside a component's
    # execution.  The drift is caught after that component; later components
    # never run, every envelope this run wrote is retained only as a
    # diagnostic no evidence loader discovers, and the attempt cannot be
    # reported as a success over a mixed-source aggregate.
    repository, plan = worktree_plan_for(tmp_path, {"README.md": "changed\n"})
    output = tmp_path / "evidence"

    def mutate_source(command, **kwargs):
        (repository / "README.md").write_text("changed during execution\n", encoding="utf-8")
        kwargs["output_path"].write_text("2 passed in 0.01s\n", encoding="utf-8")
        return ComponentExecution(tuple(command), kwargs["output_path"], 0, False)

    monkeypatch.setattr("ci.runner.execute_component", mutate_source)

    assert _synthetic_run(plan, repository, output) == 1

    diagnostic = json.loads((output / "source-drift.json").read_text(encoding="utf-8"))
    assert diagnostic["reason"] == "source_changed_during_verification"
    # Later components never ran after the drift was detected.
    assert not (output / "quality-output.log").exists()
    assert not (output / "django-output.log").exists()
    # Nothing this run wrote is discoverable as evidence.
    assert not list(output.glob("*-evidence.json"))
    assert list(output.glob("*-evidence.json.source-drift"))
    report = create_report(plan=plan, result_directory=output, phase="tester")
    assert report["verdict"] != "success"


def test_a_clean_run_succeeds_and_repository_local_evidence_does_not_self_invalidate(
    monkeypatch, tmp_path
) -> None:
    # A green run whose evidence lands under the repository's ignored .tmp
    # scratch must not trip its own source-binding checks.  Git runs for real
    # (that is the point); only the component commands are synthetic.
    from tests_ci.test_runner_timeout import green_component_commands

    repository, base, head = repository_with_change(
        tmp_path,
        {"README.md": "changed\n"},
        initial={".gitignore": ".tmp/\n", "README.md": "baseline\n"},
    )
    selection, records = selection_for(("README.md",), base=base, head=head)
    plan = build_plan(
        repository=repository,
        repository_id="DataTalksClub/website",
        base=base,
        head=head,
        selection=selection,
        records=records,
        now=datetime(2026, 8, 9, 12, tzinfo=UTC),
    )
    output = repository / ".tmp" / "verification-evidence"
    monkeypatch.setattr(
        "ci.runner.command_for",
        green_component_commands(repository, output, hang_evidence_validation=False),
    )

    assert _synthetic_run(plan, repository, output) == 0

    assert list(output.glob("*-evidence.json"))
    assert not (output / "source-drift.json").exists()
