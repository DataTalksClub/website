"""Run the repository's non-identity security evidence gate.

This is intentionally a small policy gate rather than a second security
service.  It verifies that the locked dependency, container, runtime policy,
redaction, and threat-matrix evidence exists, then runs the local dependency
consistency check.  Missing evidence is a hard failure; informational scanner
output is never treated as a pass.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

POLICY_REQUIRED_VALUES = {
    "scanner": "pip-audit-or-container-advisory-scanner",
    "severity_threshold": "high",
    "missing_severity_action": "block",
    "known_vulnerability_action": "block",
    "scanner_failure_action": "block",
    "missing_scanner_evidence_action": "block",
    "lock_check": "required",
    "environment_check": "required",
    "release_evidence": "security-check",
}


class SecurityBaselineError(RuntimeError):
    """Required non-identity security evidence is missing or failed."""


def _run(command: list[str], *, repository: Path) -> tuple[bool, str]:
    result = subprocess.run(
        command,
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
    )
    output = (result.stdout + result.stderr).strip()
    # Keep evidence bounded and avoid accidentally writing a command payload
    # containing credentials to an artifact.
    return result.returncode == 0, output[-2_000:]


def run_security_baseline(repository: Path) -> dict[str, Any]:
    required_files = (
        "uv.lock",
        "Dockerfile",
        "core/security.py",
        "core/middleware.py",
        "core/redaction.py",
        "scripts/security_artifact_scan.py",
        "scripts/security_canary_artifact.py",
        "scripts/security_vulnerability_scan.py",
        "_docs/security/dependency-vulnerability-policy.md",
        "_docs/security/dependency-vulnerability-policy.json",
        "_docs/security/non-identity-threat-control-matrix.md",
        "_docs/security/issue-141-traceability.md",
    )
    missing = [path for path in required_files if not (repository / path).is_file()]
    if missing:
        raise SecurityBaselineError(f"required security evidence is missing: {', '.join(missing)}")

    dockerfile = (repository / "Dockerfile").read_text(encoding="utf-8")
    base_settings = (repository / "website/settings/base.py").read_text(encoding="utf-8")
    production_settings = (repository / "website/settings/production.py").read_text(
        encoding="utf-8"
    )
    development_settings = (repository / "website/settings/development.py").read_text(
        encoding="utf-8"
    )
    middleware = (repository / "core/middleware.py").read_text(encoding="utf-8")
    observability = (repository / "course_management/observability/events.py").read_text(
        encoding="utf-8"
    )
    try:
        vulnerability_policy = json.loads(
            (repository / "_docs/security/dependency-vulnerability-policy.json").read_text(
                encoding="utf-8"
            )
        )
    except (json.JSONDecodeError, UnicodeError) as error:
        raise SecurityBaselineError("dependency vulnerability policy is not valid JSON") from error

    assertions: dict[str, bool] = {
        "container_uses_frozen_uv_lock": "uv sync --frozen" in dockerfile,
        "container_runs_non_root": "USER 10001:10001" in dockerfile,
        "base_has_request_boundary": "RequestBoundaryMiddleware" in base_settings,
        "base_has_csp_and_permissions_policy": (
            "Content-Security-Policy" in middleware and "Permissions-Policy" in middleware
        ),
        "base_has_cors_deny_policy": "CORS is deny-by-default" in middleware,
        "production_secure_session_cookie": "SESSION_COOKIE_SECURE = True" in production_settings,
        "production_secure_csrf_cookie": "CSRF_COOKIE_SECURE = True" in production_settings,
        "development_secure_session_cookie": "SESSION_COOKIE_SECURE = True" in development_settings,
        "development_secure_csrf_cookie": "CSRF_COOKIE_SECURE = True" in development_settings,
        "observability_uses_shared_redaction": (
            "from core.redaction import" in observability and "redact(" in observability
        ),
        "locked_dependency_present": (repository / "uv.lock").stat().st_size > 0,
        "dependency_vulnerability_policy_is_fail_closed": (
            vulnerability_policy.get("schema_version") == 1
            and all(
                vulnerability_policy.get(key) == value
                for key, value in POLICY_REQUIRED_VALUES.items()
            )
        ),
    }
    failed = [name for name, passed in assertions.items() if not passed]
    if failed:
        raise SecurityBaselineError("security policy assertions failed: " + ", ".join(failed))

    lock_ok, lock_output = _run(["uv", "lock", "--check"], repository=repository)
    dependency_ok, dependency_output = _run(
        ["uv", "pip", "check"],
        repository=repository,
    )
    if not lock_ok or not dependency_ok:
        raise SecurityBaselineError("dependency policy check failed")

    return {
        "assertions": [name for name in assertions if assertions[name]],
        "dependency": {"lock_check": "pass", "pip_check": "pass"},
        "schema_version": 1,
        "status": "pass",
        "diagnostic_lengths": {
            "lock_check": len(lock_output),
            "pip_check": len(dependency_output),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run_security_baseline(args.repository.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
