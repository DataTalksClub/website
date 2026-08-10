#!/usr/bin/env python3
"""Fail when ordinary CI or maintained Django code regains backend-specific behavior."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
ORDINARY_JOBS = ("quality", "django", "playwright", "container")
APPLICATION_ROOTS = (
    "accounts",
    "api",
    "cadmin",
    "content",
    "content_sync",
    "core",
    "course_management",
    "courses",
    "data",
    "email_app",
    "events",
    "jobs",
    "management_api",
    "management_auth",
    "studio",
)
BACKEND_PATTERNS = (
    "connection.vendor",
    "has_select_for_update",
    "select_for_update",
    "django.contrib.postgres",
    "pg_advisory",
    "runsql",
    "create trigger",
    "create function",
    "pragma ",
)
BACKEND_TEST_PATTERNS = (
    "skipunlessdbfeature",
    "connection.vendor",
    "has_select_for_update",
)


def nested_strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from nested_strings(key)
            yield from nested_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from nested_strings(item)


def check_workflow() -> list[str]:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    jobs = workflow.get("jobs", {})
    errors: list[str] = []
    for name in ORDINARY_JOBS:
        job = jobs.get(name)
        if not isinstance(job, dict):
            errors.append(f"ordinary CI job is missing: {name}")
            continue
        if job.get("services"):
            errors.append(f"ordinary CI job declares a service container: {name}")
        text = "\n".join(nested_strings(job)).lower()
        for forbidden in ("postgres", "database_url", "dtc_use_sqlite"):
            if forbidden in text:
                errors.append(f"ordinary CI job {name} contains {forbidden}")

    django_job = jobs.get("django", {})
    django_environment = django_job.get("env", {}) if isinstance(django_job, dict) else {}
    if django_environment.get("DJANGO_SETTINGS_MODULE") != "website.settings.test":
        errors.append("ordinary Django CI does not select website.settings.test")
    if django_environment.get("DTC_SQLITE_PATH"):
        errors.append("ordinary Django CI bypasses the owned test-runtime SQLite path")
    django_steps = django_job.get("steps", []) if isinstance(django_job, dict) else []
    command_lines = [
        line.strip()
        for step in django_steps
        if isinstance(step, dict)
        for line in str(step.get("run", "")).splitlines()
    ]
    required_full_commands = ("make test-factories", "make test-migrations", "make test")
    try:
        full_indices = tuple(command_lines.index(command) for command in required_full_commands)
    except ValueError:
        errors.append("ordinary Django CI does not run the owned full SQLite harness")
    else:
        if tuple(sorted(full_indices)) != full_indices:
            errors.append("ordinary Django CI runs the full SQLite harness out of order")
    return errors


def check_application() -> list[str]:
    errors: list[str] = []
    for root_name in APPLICATION_ROOTS:
        for path in (ROOT / root_name).rglob("*.py"):
            relative = path.relative_to(ROOT)
            text = path.read_text(encoding="utf-8").lower()
            is_test = "tests" in relative.parts
            patterns = BACKEND_TEST_PATTERNS if is_test else BACKEND_PATTERNS
            for pattern in patterns:
                if pattern in text:
                    errors.append(f"{relative}: backend-specific token {pattern!r}")
            if is_test and "postgresql" in path.name.lower():
                errors.append(f"{relative}: backend-specific test module name")
    return errors


def main() -> int:
    errors = [*check_workflow(), *check_application()]
    if errors:
        raise SystemExit("database portability check failed:\n- " + "\n- ".join(errors))
    print("database portability check passed for ordinary CI and maintained Django code")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
