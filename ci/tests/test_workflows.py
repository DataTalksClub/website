from __future__ import annotations

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
    script = runs(classifier)
    assert '--base "$EVENT_BEFORE"' in script
    assert '--after "$EVENT_AFTER"' in script
    assert '--github-sha "$GITHUB_SHA"' in script
    assert '--release-sha "$RELEASE_SHA"' in script
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
    assert "make test-ci-focused" in script
    selected_step = next(
        step
        for step in django["steps"]
        if step.get("name") == "Run the selected or complete Django suite"
    )
    assert selected_step["env"]["CI_SELECTION_PATH"] == ".tmp/ci-selection/ci-selection.json"
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
    }
    for name in ("auto-capture-prior", "publish", "deploy"):
        assert "ci-gate" in jobs[name]["needs"]
        assert "needs.ci-gate.result == 'success'" in jobs[name]["if"]
    assert jobs["playwright"]["steps"][-1]["run"] == "make test-playwright-core"
    assert "release-image-" in str(jobs["container"])


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
    for name in ("classification", "quality", "django", "playwright", "container", "ci-gate"):
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
        assert all(step["with"]["ref"] == "${{ github.sha }}" for step in checkout_steps)


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
    assert "make test-playwright" in runs(jobs["playwright"])
    container = runs(jobs["container"])
    assert "docker buildx build" in container
    assert "scripts.verify_static_manifest" in container
    assert "--incompatible-storage-fixture" in container
    assert "website.settings.test" not in container
    assert (
        "Static manifest verification failed: staticfiles storage does not use the runtime "
        "manifest backend" in container
    )
    assert "/health/live" in container
    assert "APP_VERSION" not in container
    assert '"source_sha": null, "image_digest": null' in container
    assert "local-development-build-version-not-configured" in container
