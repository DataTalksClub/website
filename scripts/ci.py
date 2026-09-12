#!/usr/bin/env python3
"""Run repository quality, test, and verification commands.

These commands used to be registered as a large collection of Make targets.
Keeping the orchestration in Python makes the command arguments and environment
explicit while leaving the root Makefile for the three local development entry
points documented in the README.
"""

from __future__ import annotations

import argparse
import os
import secrets
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Final

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
UV: Final = ("uv", "run", "--frozen")
TEST_MEDIA_STORE: Final = "memory"
PLAYWRIGHT_EXCLUSIONS: Final = (
    "not quarantine and not remote_readonly and not remote_mutation "
    "and not live_email and not live_provider"
)

# These lists used to live in Makefile variables. They are part of the quality
# gate, so keep them next to the command that consumes them.
ADOPTION_INTEGRATION_PYTHON: Final = (
    "accounts/managers.py",
    "accounts/tests/test_user.py",
    "api/auth.py",
    "api/models.py",
    "api/tests/test_admin_health.py",
    "scripts/build_local_review_db.py",
    "scripts/capture_screenshots.py",
    "scripts/check_database_portability.py",
    "scripts/render_course_platform_inventory.py",
    "scripts/verify_course_platform_adoption.py",
    "scripts/sync_course_platform.py",
    "scripts/prepare_course_platform_source.py",
    "accounts/backends.py",
    "accounts/identity_resolution.py",
    "accounts/middleware.py",
    "accounts/studio_authorization.py",
    "accounts/auth.py",
    "accounts/views/impersonation.py",
    "accounts/tests/test_identity_quarantine_revocation.py",
    "accounts/tests/test_email_authentication_lookup.py",
    "accounts/tests/test_cmp_learner_import_run_binding.py",
    "api/utils.py",
    "api/crud.py",
    "api/tests/test_json_body_shapes.py",
    "courses/votes.py",
    "courses/services/learner_duplicate_preflight.py",
    "courses/management/commands/learner_duplicate_preflight.py",
    "courses/tests/test_enrollment_mutation_safety.py",
    "courses/tests/test_project_vote_budget.py",
    "courses/tests/test_learner_duplicate_preflight.py",
    "courses/tests/test_time_spent_parsing_conventions.py",
    "courses/tests/test_project_submission_error_containment.py",
    "courses/tests/test_project_eval_review_binding.py",
    "courses/tests/test_cmp_learner_history_run_binding.py",
    "scripts/tests/test_sync_course_repositories_cli.py",
    "scripts/tests/test_legacy_zoomcamp_username_allocation.py",
    "scripts/tests/test_scoring_import_atomicity.py",
    "scripts/tests/test_certificate_matching.py",
    "scripts/tests/test_reviewed_release_import.py",
    "scripts/checkout_refresh.py",
    "scripts/tests/test_checkout_refresh.py",
    "scripts/rebuild_gate.py",
    "scripts/tests/test_rebuild_gate.py",
    "scripts/verify_local_dataset.py",
    "scripts/tests/test_verify_local_dataset.py",
    "scripts/ci.py",
    "scripts/content.py",
    "scripts/production_data.py",
    "scripts/seed_local_data.py",
    "scripts/tests/test_content.py",
    "scripts/tests/test_production_data.py",
    "scripts/tests/test_seed_local_data.py",
    "api/safety.py",
    "api/tests/staff_credentials.py",
    "api/tests/test_staff_authority_boundary.py",
    "courses/services/course_family_identity.py",
    "scripts/tests/test_projection_marker_provenance.py",
    "course_management/mail_preferences.py",
    "course_management/package_mail.py",
    "courses/tests/test_package_mail_flows.py",
    "accounts/tests/test_username_allocation.py",
    "accounts/services/email_verification.py",
    "courses/services/mailchimp_course_tag_import.py",
    "courses/tests/test_family_page_content.py",
    "courses/tests/test_homework_submission_learning_public_markup.py",
    "courses/tests/test_mailchimp_course_tag_import.py",
    "courses/tests/test_registration_email_verification.py",
    "scripts/build_eventbrite_descriptions.py",
    "scripts/tests/test_eventbrite_registrant_source.py",
    "scripts/tests/test_identity_manifest.py",
    "scripts/tests/test_ml_zoomcamp_2021_identity_merge.py",
    "scripts/tests/test_registrant_import.py",
)
PRODUCTION_IMPORT_PYTHON: Final = (
    "scripts/prod",
    "courses/services/cmp_content_import.py",
    "courses/services/cmp_learner_history_import.py",
)
TYPECHECK_PATHS: Final = (
    "manage.py",
    "website",
    "core",
    "content",
    "content_sync",
    "events",
    "email_app",
    "studio",
    "deploy",
    "ci",
    "test_support",
    "conftest.py",
    "sitecustomize.py",
    "review_import",
    "management_auth",
    "management_api",
    "management_registry.py",
    *ADOPTION_INTEGRATION_PYTHON,
    *PRODUCTION_IMPORT_PYTHON,
)
QUALITY_TASKS: Final = (
    "database-portability-check",
    "security-check",
    "lint",
    "format-check",
    "typecheck",
    "migrations-check",
    "django-check",
    "deployment-check",
    "test-ci",
)
TASKS: Final = {
    "setup",
    "lock-check",
    "core-source-check",
    "core-link",
    "core-unlink",
    "lint",
    "format",
    "format-check",
    "typecheck",
    "migrations-check",
    "django-check",
    "deployment-check",
    "security-check",
    "security-artifact-scan",
    "database-portability-check",
    "verify-dtc-content",
    "terraform-seo-source-check",
    "test-core",
    "check-openapi",
    "check-management-parity",
    "test-content",
    "test-course-platform-sync",
    "course-platform-source-checkout",
    "course-platform-sync-dry-run",
    "course-platform-sync",
    "test",
    "test-django-full",
    "test-ci",
    "verification-plan",
    "verification-run",
    "verification-quality",
    "verification-container",
    "verification-full",
    "verification-evidence-check",
    "verification-report-check",
    "test-ci-focused",
    "test-factories",
    "test-migrations",
    "test-playwright-core",
    "test-playwright-smoke",
    "test-playwright",
    "test-playwright-quarantined",
    "test-accessibility",
    "test-browser",
    "test-remote-readonly",
    "test-remote-mutation",
    "test-live-email",
    "test-live-provider",
    "test-all",
    "worker",
}


def _python(*arguments: str) -> tuple[str, ...]:
    return (*UV, "python", *arguments)


def _module(module: str, *arguments: str) -> tuple[str, ...]:
    return _python("-m", module, *arguments)


def _pytest(*arguments: str) -> tuple[str, ...]:
    return (*UV, "pytest", *arguments)


def _environment(**updates: str) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(updates)
    return environment


def _test_environment(*, async_unsafe: bool = False) -> dict[str, str]:
    updates = {
        "DTC_TEST_RUN_ID": os.environ.get("DTC_TEST_RUN_ID", f"ci-{os.getpid()}"),
        "DJANGO_SETTINGS_MODULE": "website.settings.test",
        "PUBLIC_MEDIA_STORE_BACKEND": os.environ.get(
            "PUBLIC_MEDIA_STORE_BACKEND", TEST_MEDIA_STORE
        ),
    }
    if async_unsafe:
        updates["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
    return _environment(**updates)


def _run(command: tuple[str, ...], *, environment: dict[str, str] | None = None) -> None:
    subprocess.run(command, cwd=PROJECT_ROOT, env=environment, check=True)


def _required_environment(*names: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for name in names:
        value = os.environ.get(name, "").strip()
        if not value:
            raise ValueError(f"{name} is required")
        values[name] = value
    return values


def _path_environment(name: str) -> str:
    return os.environ.get(name, f".tmp/{name.lower().replace('_', '-')}")


def _run_quality_task(task: str) -> None:
    if task == "database-portability-check":
        _run(_python("scripts/check_database_portability.py"))
    elif task == "security-check":
        security_directory = PROJECT_ROOT / ".tmp/security"
        security_directory.mkdir(parents=True, exist_ok=True)
        vulnerability_output = os.environ.get(
            "SECURITY_VULNERABILITY_EVIDENCE",
            ".tmp/security/security-vulnerability-scan.json",
        )
        _run(
            _module(
                "scripts.security_baseline",
                "--repository",
                ".",
                "--output",
                ".tmp/security/security-baseline.json",
            )
        )
        _run(
            _module(
                "scripts.security_vulnerability_scan",
                "--repository",
                ".",
                "--output",
                vulnerability_output,
            )
        )
        _run(
            _module(
                "scripts.security_canary_artifact",
                "--output",
                ".tmp/security/security-redaction-canary.json",
            )
        )
        _run_quality_task("security-artifact-scan")
    elif task == "security-artifact-scan":
        security_inputs = shlex.split(
            os.environ.get(
                "SECURITY_ARTIFACT_INPUTS",
                ".tmp/security/security-baseline.json "
                ".tmp/security/security-vulnerability-scan.json "
                ".tmp/security/security-redaction-canary.json",
            )
        )
        canaries = shlex.split(
            os.environ.get(
                "SECURITY_ARTIFACT_CANARIES",
                "synthetic-secret-canary synthetic-email@example.invalid synthetic-token-canary",
            )
        )
        command = list(_module("scripts.security_artifact_scan"))
        for artifact in security_inputs:
            command.extend(("--input", artifact))
        for canary in canaries:
            command.extend(("--canary", canary))
        command.extend(("--output", ".tmp/security/security-artifact-scan.json"))
        (PROJECT_ROOT / ".tmp/security").mkdir(parents=True, exist_ok=True)
        _run(tuple(command))
    elif task == "lint":
        _run(_run_ruff("check"))
    elif task == "format":
        _run(_run_ruff("format"))
    elif task == "format-check":
        _run(_run_ruff("format", "--check"))
    elif task == "typecheck":
        _run((*UV, "mypy", *TYPECHECK_PATHS))
    elif task == "migrations-check":
        _run(
            _python("manage.py", "makemigrations", "--check", "--dry-run"),
            environment=_test_environment(),
        )
    elif task == "django-check":
        _run_task("check-openapi")
        _run_task("check-management-parity")
        _run(_python("manage.py", "check"), environment=_test_environment())
    elif task == "deployment-check":
        environment = _environment(
            DTC_ENVIRONMENT="production",
            VERSION="20260809-143205-aaaaaaa",
            SOURCE_SHA="a" * 40,
            IMAGE_DIGEST="sha256:" + "b" * 64,
            DJANGO_SETTINGS_MODULE="website.settings.production",
            DJANGO_SECRET_KEY=secrets.token_urlsafe(64),
            DATABASE_URL="postgresql://check:check@127.0.0.1:5432/check",
            DJANGO_ALLOWED_HOSTS="example.invalid",
            DJANGO_CSRF_TRUSTED_ORIGINS="https://example.invalid",
            PUBLIC_MEDIA_STORE_BACKEND="s3",
            PUBLIC_MEDIA_S3_BUCKET="deployment-check-placeholder",
        )
        _run(
            _python("manage.py", "check", "--deploy", "--fail-level", "ERROR"),
            environment=environment,
        )
    elif task == "test-ci":
        _run_test_task("test-ci")
    elif task == "verify-dtc-content":
        values = _required_environment("CONTENT_CHECKOUT", "CONTENT_COMMIT")
        _run(
            _python(
                "manage.py",
                "verify_dtc_content",
                "--checkout",
                values["CONTENT_CHECKOUT"],
                "--expected-commit",
                values["CONTENT_COMMIT"],
            ),
            environment=_test_environment(),
        )
    elif task == "terraform-seo-source-check":
        values = _required_environment(
            "AWS_INFRA_REPOSITORY", "AWS_INFRA_REVISION", "AWS_INFRA_EXPECTED_COMMIT"
        )
        _run(
            _python(
                "-m",
                "scripts.verify_development_seo_terraform",
                "--repository",
                values["AWS_INFRA_REPOSITORY"],
                "--revision",
                values["AWS_INFRA_REVISION"],
                "--expected-commit",
                values["AWS_INFRA_EXPECTED_COMMIT"],
            )
        )
    else:
        raise ValueError(f"unknown quality task: {task}")


def _run_ruff(*arguments: str) -> tuple[str, ...]:
    return (*UV, "ruff", *arguments, ".", *ADOPTION_INTEGRATION_PYTHON, *PRODUCTION_IMPORT_PYTHON)


def _run_test_task(task: str, *, profile: str | None = None) -> int:
    if task in {"test", "test-django-full"}:
        _run(
            _python("manage.py", "test", "--parallel", "--noinput"),
            environment=_test_environment(),
        )
    elif task == "test-core":
        _run(
            _python(
                "manage.py",
                "test",
                "--noinput",
                "accounts",
                "core",
                "studio",
                "api",
                "management_auth",
                "management_api",
                "--parallel",
            ),
            environment=_test_environment(),
        )
    elif task == "test-content":
        _run(_python("manage.py", "test", "content.tests"), environment=_test_environment())
    elif task == "test-course-platform-sync":
        _run(_pytest("scripts/tests/test_sync_course_platform.py", "-q"))
    elif task == "test-ci":
        _run(_pytest("ci/tests", "tests_ci", "-q"))
    elif task == "test-ci-focused":
        selection = _required_environment("CI_SELECTION_PATH")["CI_SELECTION_PATH"]
        _run(
            _module("ci.focused_tests", "--selection", selection),
            environment=_test_environment(),
        )
    elif task == "test-factories":
        _run(
            _pytest(
                "test_support/tests/test_factories.py",
                "test_support/tests/test_runtime.py",
                "test_support/tests/test_safety.py",
                "test_support/tests/test_marker_registry.py",
                "-q",
            ),
            environment=_test_environment(),
        )
    elif task == "test-migrations":
        _run(
            _python(
                "manage.py",
                "test",
                "--noinput",
                "test_support.tests.test_migrations",
                "content.tests.test_editorial_route_migration_contract",
            ),
            environment=_test_environment(),
        )
    elif task in {
        "test-playwright",
        "test-playwright-core",
        "test-playwright-smoke",
        "test-playwright-quarantined",
        "test-accessibility",
    }:
        return _run_playwright(task, profile=profile)
    elif task == "test-browser":
        return _run_playwright("test-playwright", profile=profile)
    elif task in {
        "test-remote-readonly",
        "test-remote-mutation",
        "test-live-email",
        "test-live-provider",
    }:
        marker = task.removeprefix("test-")
        _run(
            _pytest("-m", marker, "-v"),
            environment=_environment(DTC_TEST_SAFETY_COMMAND=marker),
        )
    else:
        raise ValueError(f"unknown test task: {task}")
    return 0


def _run_playwright(task: str, *, profile: str | None) -> int:
    environment = _test_environment(async_unsafe=True)
    if task == "test-playwright-quarantined":
        marker = (
            "quarantine and not remote_readonly and not remote_mutation "
            "and not live_email and not live_provider"
        )
    elif task == "test-accessibility":
        marker = (
            "accessibility and not remote_readonly and not remote_mutation "
            "and not live_email and not live_provider"
        )
    else:
        selected_profile = (
            profile
            or {
                "test-playwright": "full",
                "test-playwright-core": "core",
                "test-playwright-smoke": "smoke",
            }[task]
        )
        marker = {
            "full": "(smoke or core or full) and " + PLAYWRIGHT_EXCLUSIONS,
            "core": "core and " + PLAYWRIGHT_EXCLUSIONS,
            "smoke": "smoke and " + PLAYWRIGHT_EXCLUSIONS,
        }[selected_profile]
    test_paths = ("playwright_tests",)
    if task == "test-accessibility":
        test_paths = ("playwright_tests/test_accessibility.py",)
    command = list(_pytest(*test_paths, "-p", "ci.playwright_flake_policy"))
    command.extend(("-o", "faulthandler_timeout=120", "-m", marker, "-v"))
    if task == "test-playwright-smoke":
        command = [
            "timeout",
            "--foreground",
            "--signal=TERM",
            "--kill-after=30s",
            "600s",
            *command,
        ]
    result = subprocess.run(tuple(command), cwd=PROJECT_ROOT, env=environment, check=False)
    if task == "test-playwright-quarantined" and result.returncode == 5:
        return 0
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, command)
    return 0


def _verification_value(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _verification_plan() -> None:
    output_directory = Path(_verification_value("VERIFY_OUTPUT_DIR", ".tmp/verification"))
    evidence_directory = Path(
        _verification_value("VERIFY_EVIDENCE_DIR", str(output_directory / "evidence"))
    )
    plan = _verification_value("VERIFY_PLAN", str(output_directory / "verification-plan.json"))
    output_directory.mkdir(parents=True, exist_ok=True)
    evidence_directory.mkdir(parents=True, exist_ok=True)
    base = subprocess.run(
        ("git", "rev-parse", _verification_value("VERIFY_BASE_SHA", "HEAD")),
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    head = subprocess.run(
        ("git", "rev-parse", _verification_value("VERIFY_HEAD_SHA", "HEAD")),
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    selection = output_directory / "ci-selection.json"
    _run(
        _module(
            "ci.classifier",
            "select",
            "--repository",
            ".",
            "--event",
            "push",
            "--base",
            base,
            "--after",
            head,
            "--github-sha",
            head,
            "--release-sha",
            head,
            "--output",
            str(selection),
        )
    )
    _run(
        _module(
            "ci.verification",
            "plan",
            "--repository",
            ".",
            "--base",
            base,
            "--head",
            head,
            "--selection",
            str(selection),
            "--evidence-directory",
            str(evidence_directory),
            "--consumer",
            _verification_value("VERIFY_CONSUMER", "engineer"),
            "--include-worktree",
            "--output",
            plan,
        )
    )


def _verification_evidence_check() -> None:
    plan = _verification_value("VERIFY_PLAN", ".tmp/verification/verification-plan.json")
    evidence_directory = _verification_value("VERIFY_EVIDENCE_DIR", ".tmp/verification/evidence")
    consumer = _verification_value("VERIFY_CONSUMER", "engineer")
    _run(_module("ci.verification", "validate-plan", "--plan", plan))
    _run(
        _module(
            "ci.verification",
            "validate-evidence-directory",
            "--directory",
            evidence_directory,
            "--plan",
            plan,
            "--consumer",
            consumer,
        )
    )


def _verification_report_check() -> None:
    _verification_evidence_check()
    plan = _verification_value("VERIFY_PLAN", ".tmp/verification/verification-plan.json")
    evidence_directory = _verification_value("VERIFY_EVIDENCE_DIR", ".tmp/verification/evidence")
    report = _verification_value("VERIFY_REPORT", ".tmp/verification/verification-report.json")
    phase = _verification_value("VERIFY_PHASE", os.environ.get("VERIFY_CONSUMER", "engineer"))
    pending = ("--allow-pending",) if phase == "engineer" else ()
    _run(
        _module(
            "ci.verification",
            "report",
            "--plan",
            plan,
            "--result-directory",
            evidence_directory,
            "--phase",
            phase,
            "--output",
            report,
        )
    )
    _run(
        _module(
            "ci.verification",
            "validate-report",
            "--plan",
            plan,
            "--report",
            report,
            "--evidence-directory",
            evidence_directory,
            *pending,
        )
    )


def _verification_run() -> int:
    plan = _verification_value("VERIFY_PLAN", ".tmp/verification/verification-plan.json")
    evidence_directory = _verification_value("VERIFY_EVIDENCE_DIR", ".tmp/verification/evidence")
    _run(_module("ci.verification", "validate-plan", "--plan", plan))
    command = list(
        _module(
            "ci.runner",
            "--plan",
            plan,
            "--repository",
            ".",
            "--output-directory",
            evidence_directory,
            "--worktree",
            _verification_value("VERIFY_WORKTREE", "local"),
            "--producer-role",
            _verification_value("VERIFY_PRODUCER_ROLE", "engineer"),
        )
    )
    issue = os.environ.get("VERIFY_ISSUE")
    if issue:
        command.extend(("--issue", issue))
    runner = subprocess.run(tuple(command), cwd=PROJECT_ROOT, check=False)
    try:
        _verification_report_check()
    except subprocess.CalledProcessError as error:
        return error.returncode
    return runner.returncode


def _verification_full() -> None:
    _verification_quality()
    _run(_python("manage.py", "migrate", "--noinput"), environment=_test_environment())
    _run_test_task("test-factories")
    _run_test_task("test-migrations")
    _run_test_task("test")
    _run_test_task("test-playwright", profile="full")
    _run_verification_container()


def _verification_quality() -> None:
    for task in QUALITY_TASKS:
        _run_quality_task(task)


def _run_verification_container() -> None:
    output = _verification_value(
        "VERIFY_CONTAINER_OUTPUT", ".tmp/verification/evidence/container-check.json"
    )
    _run(_module("ci.container_check", "--repository", ".", "--output", output))


def _run_course_platform_sync(*, apply: bool) -> None:
    command = [
        *_python("scripts/sync_course_platform.py"),
        "--source-ref",
        os.environ.get("CMP_SOURCE_REF", "main"),
    ]
    if repository := os.environ.get("CMP_SOURCE_REPOSITORY"):
        command.extend(("--source-repository", repository))
    if checkout := os.environ.get("CMP_SOURCE_CHECKOUT"):
        command.extend(("--source-checkout", checkout))
    command.append("--apply" if apply else "--dry-run")
    _run(tuple(command))


def _run_task(task: str, *, profile: str | None = None) -> int:
    if task in QUALITY_TASKS:
        _run_quality_task(task)
        return 0
    if task == "quality":
        _verification_quality()
        return 0
    if task == "setup":
        _run(("uv", "sync", "--locked"))
        (PROJECT_ROOT / ".tmp/screenshots").mkdir(parents=True, exist_ok=True)
        _run(("uv", "run", "playwright", "install", "chromium"))
        return 0
    if task == "lock-check":
        _run(("uv", "lock", "--check"))
        return 0
    if task == "core-source-check":
        _run(_python("scripts/check_community_base_source.py"))
        return 0
    if task in {"core-link", "core-unlink"}:
        _run(_python("scripts/community_base_link.py", task.removeprefix("core-")))
        return 0
    if task == "check-openapi":
        _run(
            _python("manage.py", "generate_admin_openapi", "--check"),
            environment=_test_environment(),
        )
        return 0
    if task == "check-management-parity":
        _run(
            _python("manage.py", "check_management_parity"),
            environment=_test_environment(),
        )
        return 0
    if task == "test-all":
        _run_task("lock-check")
        for quality_task in (
            "database-portability-check",
            "lint",
            "format-check",
            "typecheck",
            "migrations-check",
            "django-check",
            "deployment-check",
        ):
            _run_task(quality_task)
        for test_task in ("test-factories", "test-migrations", "test", "test-playwright"):
            _run_task(test_task, profile="full" if test_task == "test-playwright" else None)
        return 0
    if task in {
        "test-core",
        "test",
        "test-django-full",
        "test-content",
        "test-course-platform-sync",
        "test-ci",
        "test-ci-focused",
        "test-factories",
        "test-migrations",
        "test-playwright-core",
        "test-playwright-smoke",
        "test-playwright",
        "test-playwright-quarantined",
        "test-accessibility",
        "test-browser",
        "test-remote-readonly",
        "test-remote-mutation",
        "test-live-email",
        "test-live-provider",
    }:
        return _run_test_task(task, profile=profile)
    if task == "course-platform-source-checkout":
        _run(_python("scripts/prepare_course_platform_source.py"))
        return 0
    if task == "course-platform-sync-dry-run":
        _run_course_platform_sync(apply=False)
        return 0
    if task == "course-platform-sync":
        _run_course_platform_sync(apply=True)
        return 0
    if task == "verification-plan":
        _verification_plan()
        return 0
    if task == "verification-run":
        return _verification_run()
    if task == "verification-quality":
        _verification_quality()
        return 0
    if task == "verification-container":
        _run_verification_container()
        return 0
    if task == "verification-full":
        _verification_full()
        return 0
    if task == "verification-evidence-check":
        _verification_evidence_check()
        return 0
    if task == "verification-report-check":
        _verification_report_check()
        return 0
    if task == "worker":
        _run(_python("manage.py", "run_job_worker"))
        return 0
    raise ValueError(f"unknown task: {task}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", choices=sorted((*TASKS, "quality")))
    parser.add_argument("--profile", choices=("smoke", "core", "full"))
    args = parser.parse_args(argv)
    try:
        return _run_task(args.task, profile=args.profile)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        if isinstance(error, subprocess.CalledProcessError):
            return error.returncode or 1
        print(f"ci command failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
