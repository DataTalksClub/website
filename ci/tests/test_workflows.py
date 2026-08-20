from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]


def workflow(name: str) -> dict[str, Any]:
    return yaml.load(
        (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )


def runs(job: dict[str, Any]) -> str:
    return "\n".join(
        step["run"] for step in job["steps"] if isinstance(step, dict) and "run" in step
    )


def logical_shell_commands(job: dict[str, Any]) -> list[str]:
    commands: list[str] = []
    continuation: list[str] = []
    for raw_line in runs(job).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        continued = line.endswith("\\")
        continuation.append(line.removesuffix("\\").rstrip())
        if not continued:
            commands.append(" ".join(continuation))
            continuation = []
    assert not continuation
    return commands


def verification_invocations(job: dict[str, Any], subcommand: str) -> list[str]:
    marker = f"python -m ci.verification {subcommand}"
    return [command for command in logical_shell_commands(job) if marker in command]


def test_normal_workflow_keeps_release_concurrency_and_exact_base_contract() -> None:
    data = workflow("ci.yml")
    concurrency = data["concurrency"]
    assert concurrency == {
        "group": "website-development-release",
        "cancel-in-progress": "false",
    }
    jobs = data["jobs"]
    classifier = jobs["classification"]
    assert classifier["needs"] == "resolve-release"
    assert classifier["outputs"]["profile"] == "${{ steps.selection.outputs.profile }}"
    assert (
        classifier["outputs"]["created_attempt"] == "${{ steps.selection.outputs.created_attempt }}"
    )
    assert (
        classifier["outputs"]["selection_sha256"]
        == "${{ steps.selection.outputs.selection_sha256 }}"
    )
    assert classifier["outputs"]["screenshots_mode"] == "${{ steps.plan.outputs.screenshots_mode }}"
    script = runs(classifier)
    assert '--base "$EVENT_BEFORE"' in script
    assert '--after "$EVENT_AFTER"' in script
    assert '--github-sha "$GITHUB_SHA"' in script
    assert '--release-sha "$RELEASE_SHA"' in script
    assert "--provenance-output .tmp/ci-selection/ci-selection-provenance.json" in script
    assert '--run-id "$RUN_ID"' in script
    assert '--created-attempt "$RUN_ATTEMPT"' in script
    assert '--source-before-sha "$EVENT_BEFORE"' in script
    assert '--source-after-sha "$EVENT_AFTER"' in script
    assert "--find-renames" not in script  # Git parsing is code-owned, not inline shell.
    checkouts = [step for step in classifier["steps"] if step.get("uses") == "actions/checkout@v4"]
    assert checkouts[0]["with"]["ref"] == "${{ github.sha }}"
    assert checkouts[1]["with"]["fetch-depth"] == "0"
    assert "--repository ../release-source" in script


def test_selected_django_always_uses_fresh_sqlite_and_validated_closed_runner() -> None:
    jobs = workflow("ci.yml")["jobs"]
    assert not any("services" in job for job in jobs.values())
    django = jobs["django"]
    assert set(django["needs"]) == {"resolve-release", "classification"}
    assert "DTC_SQLITE_PATH" not in django["env"]
    script = runs(django)
    assert "rm -f .tmp/ci.sqlite3" not in script
    assert "manage.py migrate --noinput" not in script
    assert "ci.classifier validate" in script
    assert "ci.provenance resolve" in script
    assert "attempt-1" in script
    assert "current-payload" in script
    assert "attempt-1-payload" in script
    assert "ci-selection.json ci-selection-provenance.json" in script
    assert "--current-directory ../release-source/.tmp/ci-selection/current-payload" in script
    assert "--fallback-directory ../release-source/.tmp/ci-selection/attempt-1-payload" in script
    assert '--expected-selection-sha256 "$SELECTION_SHA256"' in script
    assert "make test-ci-focused" in script
    selected_step = next(
        step
        for step in django["steps"]
        if step.get("name") == "Run the selected or complete Django suite"
    )
    assert (
        selected_step["env"]["CI_SELECTION_PATH"] == ".tmp/ci-selection/resolved/ci-selection.json"
    )
    assert "make test-factories" in script
    assert "make test-migrations" in script
    assert "make test" in script
    assert "postgres" not in script.lower()
    validation = next(
        step
        for step in django["steps"]
        if step.get("name") == "Validate the code-owned test selection"
    )
    assert validation["working-directory"] == ".tmp/ci-controller"
    quality = jobs["quality"]
    release_quality = next(
        step
        for step in quality["steps"]
        if step.get("name")
        == "Run the current versioned quality contract against the selected release"
    )
    assert release_quality["working-directory"] == ".tmp/ci-controller"
    assert "ci.quality_contract" in release_quality["run"]
    assert "--repository ../release-source" in release_quality["run"]
    assert "make verification-quality" not in release_quality["run"]
    contract_step = next(
        step
        for step in quality["steps"]
        if step.get("name") == "Run CI orchestration contract tests from the workflow controller"
    )
    assert contract_step["working-directory"] == ".tmp/ci-controller"


def test_aggregate_gate_is_the_release_dependency() -> None:
    jobs = workflow("ci.yml")["jobs"]
    gate = jobs["ci-gate"]
    assert "always()" in gate["if"]
    assert set(gate["needs"]) == {
        "resolve-release",
        "classification",
        "quality",
        "django",
        "playwright",
        "container",
        "screenshots",
    }
    gate_script = runs(gate)
    assert "ci.provenance resolve" in gate_script
    assert "--evidence .tmp/ci-selection/resolved/ci-selection-resolution.json" in gate_script
    assert "attempt-1" in gate_script
    assert "current-payload" in gate_script
    assert "attempt-1-payload" in gate_script
    assert "ci-selection.json ci-selection-provenance.json" in gate_script
    assert "--current-directory .tmp/ci-selection/current-payload" in gate_script
    assert "--fallback-directory .tmp/ci-selection/attempt-1-payload" in gate_script
    assert "needs.classification.outputs.created_attempt == '1'" in str(gate)
    assert '--expected-event "$EVENT_NAME"' in gate_script
    assert '--expected-source-after-sha "$EVENT_AFTER"' in gate_script
    assert '--expected-source-before-sha "$EVENT_BEFORE"' in gate_script
    assert '--expected-selection-sha256 "$SELECTION_SHA256"' in gate_script
    for name in ("auto-capture-prior", "publish", "deploy"):
        assert "ci-gate" in jobs[name]["needs"]
        assert "needs.ci-gate.result == 'success'" in jobs[name]["if"]
    playwright = jobs["playwright"]
    assert set(playwright["needs"]) == {"resolve-release", "classification"}
    assert playwright["timeout-minutes"] == "60"
    assert "make test-playwright-core" in runs(playwright)
    assert "make test-playwright" in runs(playwright)
    assert "playwright_mode == 'rerun'" in str(playwright)
    assert "release-image-" in str(jobs["container"])


def test_container_jobs_establish_locked_environments_before_recording() -> None:
    normal = workflow("ci.yml")["jobs"]["container"]
    normal_sync = next(
        step
        for step in normal["steps"]
        if step.get("name") == "Establish the locked controller environment"
    )
    normal_lock = next(
        step for step in normal["steps"] if step.get("name") == "Verify the controller lockfile"
    )
    assert normal_sync["working-directory"] == ".tmp/ci-controller"
    assert normal_sync["run"] == "uv sync --locked"
    assert normal_lock["working-directory"] == ".tmp/ci-controller"
    assert normal_lock["run"] == "uv lock --check"
    normal_script = runs(normal)
    assert normal_script.index("uv sync --locked") < normal_script.index(
        "ci.verification environment"
    )
    assert normal_script.index("uv lock --check") < normal_script.index(
        "ci.verification environment"
    )

    scheduled = workflow("scheduled-full-regression.yml")["jobs"]["container"]
    scheduled_script = runs(scheduled)
    assert "uv sync --locked" in scheduled_script
    assert "uv lock --check" in scheduled_script
    assert scheduled_script.index("uv sync --locked") < scheduled_script.index(
        "ci.verification environment"
    )
    assert scheduled_script.index("uv lock --check") < scheduled_script.index(
        "ci.verification environment"
    )


def test_normal_workflow_uses_versioned_plan_and_trusted_evidence_artifact() -> None:
    data = workflow("ci.yml")
    assert data["permissions"] == {"contents": "read", "actions": "read"}
    jobs = data["jobs"]
    classifier = jobs["classification"]
    assert "verification_profile" in classifier["outputs"]
    assert "playwright_mode" in classifier["outputs"]
    assert "screenshots_mode" in classifier["outputs"]
    classifier_script = runs(classifier)
    assert "ci.history" in classifier_script
    assert "ci.verification plan" in classifier_script
    assert "--release-requires-image" in classifier_script
    assert "--component selector" in classifier_script
    assert "ci.verification environment" in classifier_script
    assert "quality compatibility content_invariants evidence_validation" in runs(jobs["quality"])
    assert "quality-contract-v1" in (ROOT / "ci" / "ownership.json").read_text(encoding="utf-8")
    workflow_text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert workflow_text.count("ci.verification record") == workflow_text.count("--machine-output")
    assert workflow_text.count("ci.verification record") == workflow_text.count(
        "--execution-environment"
    )
    assert "django-output.log" in workflow_text
    assert "playwright-output.log" in workflow_text
    assert "container-check.json" in workflow_text
    assert "screenshots.json" in workflow_text
    assert "2>&1 | tee" in workflow_text

    gate = jobs["ci-gate"]
    screenshots = jobs["screenshots"]
    assert set(screenshots["needs"]) == {"resolve-release", "classification"}
    screenshot_script = runs(screenshots)
    assert "ci.screenshot_runtime" in screenshot_script
    assert "--repository ." in screenshot_script
    assert "--controller-repository ../ci-controller" in screenshot_script
    assert (
        "--server-log ../ci-controller/.tmp/components-screenshots/server.log" in screenshot_script
    )
    assert "playwright install --with-deps chromium" in screenshot_script
    assert "--screenshot .tmp/components-screenshots/screenshots.json" in screenshot_script
    assert "--component screenshots" in screenshot_script
    assert ".components.screenshots.command" in screenshot_script
    assert "screenshots_mode == 'rerun'" in str(screenshots)
    assert "No render-impact changes" in screenshot_script
    screenshot_runtime_steps = [
        step
        for step in screenshots["steps"]
        if step.get("name") == "Migrate and capture with one owned SQLite runtime"
    ]
    assert len(screenshot_runtime_steps) == 1
    assert not any(
        step.get("name")
        in {
            "Migrate the synthetic SQLite database",
            "Start the release application for local capture",
        }
        for step in screenshots["steps"]
    )
    screenshot_record = next(
        step
        for step in screenshots["steps"]
        if step.get("name") == "Record screenshot evidence with the job's actual environment"
    )
    assert screenshot_record["if"].startswith("always()")
    assert (
        "--machine-output .tmp/components-screenshots/screenshots.json" in screenshot_record["run"]
    )
    assert "--screenshot .tmp/components-screenshots/screenshots.json" in screenshot_record["run"]
    assert "artifact_args" in screenshot_record["run"]
    screenshot_artifact = (
        "verification-component-screenshots-${{ github.run_id }}-attempt-${{ github.run_attempt }}"
    )
    assert screenshot_artifact in str(screenshots)
    assert set(gate["needs"]) == {
        "resolve-release",
        "classification",
        "quality",
        "django",
        "playwright",
        "container",
        "screenshots",
    }
    gate_script = runs(gate)
    assert "ci.verification report" in gate_script
    assert "--verification-plan" in gate_script
    assert "--verification-report" in gate_script
    assert "--verification-evidence-directory" in gate_script
    assert "verification-evidence-${{ github.run_id }}-attempt-${{ github.run_attempt }}" in str(
        gate
    )


def test_deploy_smoke_has_exact_readonly_authority_and_pinned_base_url() -> None:
    deploy = workflow("ci.yml")["jobs"]["deploy"]
    release = next(step for step in deploy["steps"] if step.get("id") == "release")

    assert {
        name: release["env"][name]
        for name in (
            "DTC_TEST_SAFETY_COMMAND",
            "DTC_TEST_TARGET_CLASS",
            "DTC_TEST_REMOTE_NAMESPACE",
        )
    } == {
        "DTC_TEST_SAFETY_COMMAND": "remote_readonly",
        "DTC_TEST_TARGET_CLASS": "isolated_development",
        "DTC_TEST_REMOTE_NAMESPACE": "deploy-${{ github.run_id }}-${{ github.run_attempt }}",
    }
    assert "DTC_TEST_BASE_URL" not in release["env"]
    assert "--base-url https://web.dtcdev.click" in release["run"]


def test_manual_release_is_full_and_probe_contract_stays_separate() -> None:
    jobs = workflow("ci.yml")["jobs"]
    assert "manual_dispatch" in (ROOT / "ci" / "classifier.py").read_text(encoding="utf-8")
    for name in (
        "classification",
        "quality",
        "django",
        "playwright",
        "screenshots",
        "container",
        "ci-gate",
    ):
        assert "operation != 'probe'" in jobs[name]["if"]
    for name in (
        "probe-contract",
        "probe-publisher",
        "probe-deployer",
        "probe-wrong-main-claims",
        "probe-wrong-environment-claim",
    ):
        assert "operation == 'probe'" in jobs[name]["if"]
        assert "ci-gate" not in jobs[name].get("needs", [])


def test_scheduled_workflow_has_exact_trigger_queue_and_least_permissions() -> None:
    data = workflow("scheduled-full-regression.yml")
    assert data["on"] == {"schedule": [{"cron": "17 */4 * * *"}]}
    assert data["permissions"] == {"contents": "read", "actions": "read"}
    assert data["concurrency"] == {
        "group": "website-scheduled-full-regression",
        "cancel-in-progress": "false",
        "queue": "max",
    }
    assert not any("permissions" in job for job in data["jobs"].values())


def test_cmp_upstream_workflow_is_manual_or_scheduled_and_read_only() -> None:
    data = workflow("cmp-upstream-sync.yml")
    assert data["on"]["schedule"] == [{"cron": "31 */4 * * *"}]
    assert "workflow_dispatch" in data["on"]
    assert data["on"]["workflow_dispatch"]["inputs"]["source_ref"]["default"] == "main"
    assert data["permissions"] == {"contents": "read"}
    assert data["concurrency"] == {
        "group": "website-cmp-upstream-drift",
        "cancel-in-progress": "false",
    }
    assert set(data["jobs"]) == {"drift"}
    job = data["jobs"]["drift"]
    script = runs(job)
    assert "scripts/sync_course_platform.py" in script
    assert "--dry-run" in script
    assert "--apply" not in script
    workflow_text = (ROOT / ".github" / "workflows" / "cmp-upstream-sync.yml").read_text(
        encoding="utf-8"
    )
    assert "git push" not in workflow_text
    assert "gh " not in workflow_text
    assert "deploy" not in workflow_text.lower()
    assert any(step.get("uses") == "actions/upload-artifact@v4" for step in job["steps"])


def test_scheduled_workflow_has_no_mutation_or_aws_jobs_and_checks_exact_sha() -> None:
    data = workflow("scheduled-full-regression.yml")
    jobs = data["jobs"]
    assert set(jobs) == {
        "selector",
        "quality",
        "factories",
        "migrations",
        "django",
        "playwright",
        "container",
        "full-regression",
        "scheduled-gate",
    }
    text = (ROOT / ".github" / "workflows" / "scheduled-full-regression.yml").read_text(
        encoding="utf-8"
    )
    assert "aws-actions" not in text
    assert "id-token" not in text
    assert "environment:" not in text
    assert not any("services" in job for job in jobs.values())
    assert '--current-ref "$GITHUB_REF"' in runs(jobs["selector"])
    for job in jobs.values():
        checkout_steps = [
            step for step in job["steps"] if step.get("uses") == "actions/checkout@v4"
        ]
        assert checkout_steps
        assert all(
            step["with"]
            == {
                "ref": "${{ github.sha }}",
                "fetch-depth": "0",
            }
            for step in checkout_steps
        )


def test_scheduled_evidence_capture_and_record_symmetrically_allow_runner_drift() -> None:
    jobs = workflow("scheduled-full-regression.yml")["jobs"]
    evidence_jobs = {"selector", "quality", "django", "playwright", "container"}
    assert {
        name
        for name, job in jobs.items()
        if verification_invocations(job, "environment") or verification_invocations(job, "record")
    } == evidence_jobs

    for name in evidence_jobs:
        environment_calls = verification_invocations(jobs[name], "environment")
        record_calls = verification_invocations(jobs[name], "record")
        assert len(environment_calls) == 1, name
        assert len(record_calls) == 1, name
        assert environment_calls[0].split().count("--allow-hosted-runner-drift") == 1, name
        assert record_calls[0].split().count("--allow-hosted-runner-drift") == 1, name

    assert "for component in quality evidence_validation; do" in runs(jobs["quality"])
    for name in ("selector", "django", "playwright", "container"):
        assert f"--component {name}" in verification_invocations(jobs[name], "environment")[0]


def test_scheduled_aggregate_state_explicitly_allows_hosted_runner_drift() -> None:
    jobs = workflow("scheduled-full-regression.yml")["jobs"]
    invocations = verification_invocations(jobs["full-regression"], "scheduled-state")
    assert len(invocations) == 1
    assert invocations[0].split().count("--allow-hosted-runner-drift") == 1


def test_scheduled_playwright_executes_and_records_the_planner_core_command() -> None:
    playwright = workflow("scheduled-full-regression.yml")["jobs"]["playwright"]
    planner_command = json.loads((ROOT / "ci" / "ownership.json").read_text(encoding="utf-8"))[
        "components"
    ]["playwright"]["command"]
    assert planner_command == "make test-playwright-core"
    command_pattern = re.compile(r"\bmake test-playwright(?:-core)?\b")

    execution = next(
        step
        for step in playwright["steps"]
        if step.get("name") == "Run and retain the complete Playwright output"
    )
    recording = next(
        step
        for step in playwright["steps"]
        if step.get("name") == "Record shared Playwright envelope"
    )
    assert command_pattern.findall(execution["run"]) == [planner_command]
    assert command_pattern.findall(recording["run"]) == [planner_command]
    assert f'--command "{planner_command}"' in recording["run"]


def test_scheduled_full_marker_and_gate_cover_every_component_or_exact_skip() -> None:
    jobs = workflow("scheduled-full-regression.yml")["jobs"]
    assert jobs["full-regression"]["name"] == "full-regression"
    assert "always()" in jobs["full-regression"]["if"]
    assert set(jobs["full-regression"]["needs"]) == {
        "selector",
        "quality",
        "factories",
        "migrations",
        "django",
        "playwright",
        "container",
    }
    assert jobs["scheduled-gate"]["if"] == "always()"
    assert "ci.gate scheduled" in runs(jobs["scheduled-gate"])
    assert "DTC_SQLITE_PATH" not in jobs["django"]["env"]
    assert "make test-factories" in runs(jobs["factories"])
    assert "make test-migrations" in runs(jobs["migrations"])
    assert "make test" in runs(jobs["django"])
    assert "make test-playwright-core" in runs(jobs["playwright"])
    assert "ci.quality_contract" in runs(jobs["quality"])
    assert "make verification-quality" not in runs(jobs["quality"])
    container = runs(jobs["container"])
    assert "docker buildx build" in container
    assert "--incompatible-storage-fixture" in container
    assert "website.settings.test" not in container
    assert "local-development-build-version-not-configured" in container
    aggregate = runs(jobs["full-regression"])
    assert "ci.verification validate-evidence-directory" in aggregate
    assert "ci.verification report" in aggregate
    assert "ci.verification scheduled-state" in aggregate
    assert "--directory .tmp" in aggregate
    assert "verification-evidence-${{ github.run_id }}-attempt-${{ github.run_attempt }}" in str(
        jobs["full-regression"]
    )
    workflow_text = (ROOT / ".github" / "workflows" / "scheduled-full-regression.yml").read_text(
        encoding="utf-8"
    )
    assert workflow_text.count("ci.verification record") == workflow_text.count("--machine-output")
    assert workflow_text.count("ci.verification record") == workflow_text.count(
        "--execution-environment"
    )
    assert "quality-output.log" in workflow_text
    assert "django-output.log" in workflow_text
    assert "playwright-output.log" in workflow_text
    assert "container-check.json" in workflow_text
