from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import re
import stat
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

from ci.ownership import matches_any, sha256_json

EVIDENCE_SCHEMA_VERSION = 3
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")
RESULTS = frozenset(
    {
        "action_required",
        "cancelled",
        "failure",
        "skipped",
        "stale",
        "success",
        "timed_out",
    }
)
INPUT_GROUPS = ("configuration", "fixtures", "source", "tests", "tools")
ALLOWLISTED_CONFIG = (
    "DJANGO_ALLOW_ASYNC_UNSAFE",
    "DJANGO_SETTINGS_MODULE",
    "DTC_ENVIRONMENT",
    "VERIFICATION_ENVIRONMENT_REVISION",
)
VALIDITY_SECONDS = {
    "live": 4 * 60 * 60,
    "standard": 7 * 24 * 60 * 60,
    "visual": 7 * 24 * 60 * 60,
    "volatile": 24 * 60 * 60,
}
ALLOWED_COMPONENT_COMMANDS = {
    "compatibility": frozenset(
        {
            "make compatibility-source-artifacts-check compatibility-artifacts-check "
            "check-links check-seo"
        }
    ),
    "container": frozenset({"make verification-container", "exact release image verification"}),
    "content_invariants": frozenset({"make verification-content-invariants"}),
    "django": frozenset({"make test", "make test-ci-focused"}),
    "evidence_validation": frozenset({"make test-ci"}),
    "playwright": frozenset({"make test-playwright-core", "make test-playwright"}),
    "quality": frozenset({"quality-contract-v1"}),
    "screenshots": frozenset({"independent tester desktop/mobile capture and inspection"}),
    "selector": frozenset(
        {"make verification-plan", "ci.classifier select and ci.verification plan"}
    ),
}
TEST_OUTPUT_COMPONENTS = frozenset(
    {"compatibility", "django", "evidence_validation", "playwright", "quality"}
)
OUTPUT_FORMATS = {
    "compatibility": frozenset({"test-log-v1"}),
    "container": frozenset({"container-check-v1"}),
    "content_invariants": frozenset({"content-invariants-v1"}),
    "django": frozenset({"test-log-v1"}),
    "evidence_validation": frozenset({"test-log-v1"}),
    "playwright": frozenset({"test-log-v1"}),
    "quality": frozenset({"test-log-v1"}),
    "screenshots": frozenset({"screenshot-artifact-v1"}),
    "selector": frozenset({"verification-plan-v3"}),
}
PYTEST_OUTCOME_RE = re.compile(
    r"(?P<count>[0-9]+) (?P<outcome>passed|failed|skipped|errors?|xfailed|xpassed)\b"
)
PYTEST_DURATION_RE = re.compile(r"\bin [0-9]+(?:\.[0-9]+)?s\b")
UNITTEST_RAN_RE = re.compile(r"^Ran (?P<count>[0-9]+) tests? in ", re.MULTILINE)
UNITTEST_SKIPPED_RE = re.compile(r"skipped=(?P<count>[0-9]+)")
UNITTEST_FAILURE_RE = re.compile(r"(?:failures|errors)=(?P<count>[0-9]+)")
TRUSTED_CI_JOB_COMPONENTS = {
    "classification": frozenset({"selector"}),
    "container": frozenset({"container"}),
    "django": frozenset({"django"}),
    "playwright": frozenset({"playwright"}),
    "quality": frozenset({"compatibility", "content_invariants", "evidence_validation", "quality"}),
    "screenshots": frozenset({"screenshots"}),
}
MAX_EVIDENCE_FILES = 500
MAX_EVIDENCE_BYTES = 8 * 1024 * 1024


class EvidenceError(ValueError):
    """Evidence is malformed, untrusted, stale, or not bound to the candidate inputs."""


def utc_now() -> datetime:
    return datetime.now(tz=UTC).replace(microsecond=0)


def isoformat(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise EvidenceError(f"{field} must be an RFC3339 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise EvidenceError(f"{field} must be an RFC3339 UTC timestamp") from exc
    if parsed.tzinfo is None:
        raise EvidenceError(f"{field} must include timezone information")
    return parsed.astimezone(UTC)


def environment_fingerprint(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    environ = os.environ if environ is None else environ
    hosted_runner = "ImageOS" in environ or "RUNNER_OS" in environ
    runner_image = environ.get("ImageOS", environ.get("RUNNER_OS", "local"))
    image_version = environ.get("ImageVersion")
    if hosted_runner and not image_version:
        raise EvidenceError("hosted runner fingerprint requires ImageVersion")
    image_version = image_version or "local"
    runner_image = f"{runner_image}@{image_version}"
    payload = {
        "allowlisted_config": {
            key: environ[key] for key in ALLOWLISTED_CONFIG if key in environ and environ[key]
        },
        "architecture": platform.machine() or "unknown",
        "browser": environ.get("VERIFICATION_BROWSER", "chromium"),
        "database": environ.get("VERIFICATION_DATABASE", "sqlite"),
        "django": _package_version("django"),
        "operating_system": platform.system().lower() or "unknown",
        "playwright": _package_version("playwright"),
        "python": platform.python_version(),
        # GitHub's ImageOS is a moving label. ImageVersion is the concrete
        # hosted-runner image revision and therefore part of evidence identity.
        "runner_image": runner_image,
        "runner_image_version": image_version,
        "uv": _uv_version(),
    }
    return payload | {"sha256": sha256_json(payload)}


def environment_matches_plan(
    actual: Mapping[str, Any],
    planned: Mapping[str, Any],
    *,
    allow_hosted_runner_drift: bool = False,
) -> bool:
    """Compare an execution environment with its plan-bound environment.

    GitHub-hosted jobs in one workflow can be assigned different patch revisions of the
    same Ubuntu image while the jobs are queued.  The concrete revision remains part of
    each fingerprint (and therefore still prevents cross-run evidence reuse), but a caller
    may explicitly allow this same-workflow drift when recording fresh hosted evidence.
    """

    actual_environment = validate_environment_fingerprint(actual)
    planned_environment = validate_environment_fingerprint(planned)
    if actual_environment == planned_environment:
        return True
    if not allow_hosted_runner_drift:
        return False
    actual_family, _, actual_version = actual_environment["runner_image"].partition("@")
    planned_family, _, planned_version = planned_environment["runner_image"].partition("@")
    if (
        not actual_version
        or not planned_version
        or actual_family == "local"
        or planned_family == "local"
        or actual_family != planned_family
    ):
        return False
    comparable_actual = {
        key: value
        for key, value in actual_environment.items()
        if key not in {"runner_image", "runner_image_version", "sha256"}
    }
    comparable_planned = {
        key: value
        for key, value in planned_environment.items()
        if key not in {"runner_image", "runner_image_version", "sha256"}
    }
    return comparable_actual == comparable_planned


def git_manifest(
    repository: str | Path, revision: str
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    if not isinstance(revision, str) or not SHA_RE.fullmatch(revision):
        raise EvidenceError("revision must be a full lowercase Git object id")
    repository = Path(repository)
    tree_oid = _git(repository, "rev-parse", "--verify", f"{revision}^{{tree}}").decode().strip()
    object_format = _git(repository, "rev-parse", "--show-object-format").decode().strip()
    raw = _git(repository, "ls-tree", "-r", "-z", revision)
    entries: list[dict[str, str]] = []
    for record in raw[:-1].split(b"\0") if raw else ():
        metadata, separator, raw_path = record.partition(b"\t")
        fields = metadata.split(b" ")
        if not separator or len(fields) != 3:
            raise EvidenceError("Git tree contains a malformed entry")
        mode, object_type, object_id = fields
        if object_type not in {b"blob", b"commit"}:
            raise EvidenceError("Git tree contains an unsupported object type")
        path = raw_path.decode("utf-8", errors="surrogateescape")
        _safe_repository_path(path)
        entries.append(
            {
                "mode": mode.decode("ascii"),
                "object_id": object_id.decode("ascii"),
                "object_type": object_type.decode("ascii"),
                "path": path,
            }
        )
    if entries != sorted(entries, key=lambda item: item["path"]):
        raise EvidenceError("Git manifest is not deterministically ordered")
    manifest_digest = digest_manifest(entries)
    return entries, {
        "commit": revision,
        "entry_count": len(entries),
        "git_object_algorithm": object_format,
        "manifest_sha256": manifest_digest,
        "tree_oid": tree_oid,
    }


def worktree_manifest(
    repository: str | Path, head: str
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    if not isinstance(head, str) or not SHA_RE.fullmatch(head):
        raise EvidenceError("worktree head must be a full lowercase Git object id")
    repository = Path(repository)
    actual_head = _git(repository, "rev-parse", "HEAD").decode().strip()
    if actual_head != head:
        raise EvidenceError("worktree head does not match the requested source head")
    cached: dict[str, tuple[str, str]] = {}
    raw_cached = _git(repository, "ls-files", "--stage", "-z")
    for record in raw_cached[:-1].split(b"\0") if raw_cached else ():
        metadata, separator, raw_path = record.partition(b"\t")
        fields = metadata.split(b" ")
        if not separator or len(fields) != 3 or fields[2] != b"0":
            raise EvidenceError("worktree index contains a malformed or unresolved entry")
        path = raw_path.decode("utf-8", errors="surrogateescape")
        _safe_repository_path(path)
        cached[path] = (fields[0].decode("ascii"), fields[1].decode("ascii"))
    raw_untracked = _git(repository, "ls-files", "--others", "--exclude-standard", "-z")
    paths = set(cached)
    paths.update(
        value.decode("utf-8", errors="surrogateescape")
        for value in raw_untracked[:-1].split(b"\0")
        if value
    )
    entries: list[dict[str, str]] = []
    for path in sorted(paths):
        _safe_repository_path(path)
        destination = repository / path
        cached_mode, cached_oid = cached.get(path, ("", ""))
        if cached_mode == "160000" and destination.is_dir():
            entries.append(
                {
                    "mode": cached_mode,
                    "object_id": cached_oid,
                    "object_type": "commit",
                    "path": path,
                }
            )
            continue
        try:
            stat_result = destination.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(stat_result.st_mode):
            body = os.fsencode(os.readlink(destination))
            mode = "120000"
        elif stat.S_ISREG(stat_result.st_mode):
            body = destination.read_bytes()
            mode = "100755" if stat_result.st_mode & stat.S_IXUSR else "100644"
        else:
            raise EvidenceError("worktree contains an unsupported tracked object")
        object_id = (
            subprocess.run(
                ["git", "-C", os.fspath(repository), "hash-object", "--stdin"],
                input=body,
                check=True,
                capture_output=True,
            )
            .stdout.decode("ascii")
            .strip()
        )
        entries.append(
            {
                "mode": mode,
                "object_id": object_id,
                "object_type": "blob",
                "path": path,
            }
        )
    manifest_digest = digest_manifest(entries)
    object_format = _git(repository, "rev-parse", "--show-object-format").decode().strip()
    tree_oid = _git(repository, "rev-parse", "--verify", f"{head}^{{tree}}").decode().strip()
    return entries, {
        "commit": head,
        "entry_count": len(entries),
        "git_object_algorithm": object_format,
        "manifest_sha256": manifest_digest,
        "tree_oid": tree_oid,
    }


def digest_manifest(entries: Sequence[Mapping[str, str]]) -> str:
    if not isinstance(entries, (list, tuple)):
        raise EvidenceError("manifest must be a list or tuple")
    digest = hashlib.sha256()
    previous: str | None = None
    for raw in entries:
        if not isinstance(raw, Mapping) or set(raw) != {
            "mode",
            "object_id",
            "object_type",
            "path",
        }:
            raise EvidenceError("manifest entry has an invalid shape")
        path = raw["path"]
        _safe_repository_path(path)
        if previous is not None and path <= previous:
            raise EvidenceError("manifest paths must be sorted and unique")
        previous = path
        for field in ("path", "mode", "object_type", "object_id"):
            raw_value = raw[field]
            if not isinstance(raw_value, str):
                raise EvidenceError("manifest values must be strings")
            value = raw_value.encode("utf-8", errors="surrogateescape")
            digest.update(len(value).to_bytes(8, "big"))
            digest.update(value)
    return digest.hexdigest()


def component_inputs(
    entries: Sequence[Mapping[str, str]], patterns: Iterable[str]
) -> dict[str, Any]:
    selected = [dict(entry) for entry in entries if matches_any(entry["path"], patterns)]
    groups: dict[str, Any] = {}
    for name in INPUT_GROUPS:
        group_entries = [entry for entry in selected if input_group(entry["path"]) == name]
        groups[name] = {
            "count": len(group_entries),
            "manifest_sha256": digest_manifest(group_entries),
        }
    return {
        "aggregate_sha256": digest_manifest(selected),
        "groups": groups,
        "manifest": selected,
    }


def input_group(path: str) -> str:
    parts = PurePosixPath(path).parts
    name = parts[-1]
    if (
        "tests" in parts
        or "tests_ci" in parts
        or path.startswith(("playwright_tests/", "ci/tests/", "compatibility/tests/"))
        or name.startswith("test_")
        or name == "conftest.py"
    ):
        return "tests"
    if set(parts) & {"fixtures", "golden", "snapshots"}:
        return "fixtures"
    root_tools = {".python-version", "Dockerfile", "Makefile", "pyproject.toml", "uv.lock"}
    if path in root_tools or path.startswith(".github/workflows/"):
        return "tools"
    if path.startswith("ci/") or "settings" in parts or name.endswith((".toml", ".yaml", ".yml")):
        return "configuration"
    return "source"


def build_envelope(
    *,
    plan: Mapping[str, Any],
    component: str,
    result: str,
    origin: Mapping[str, Any],
    command: str,
    execution_environment: Mapping[str, Any],
    artifacts: Sequence[Mapping[str, Any]] = (),
    machine_output: Mapping[str, Any] | None = None,
    counts: Mapping[str, int] | None = None,
    exit_code: int | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    screenshot: Mapping[str, Any] | None = None,
    supersedes: str | None = None,
    allow_hosted_runner_drift: bool = False,
) -> dict[str, Any]:
    if component not in plan.get("components", {}):
        raise EvidenceError("component is absent from the verification plan")
    component_plan = plan["components"][component]
    if result not in RESULTS:
        raise EvidenceError("result is not recognized")
    completed_at = completed_at or utc_now()
    # A caller recording an already-completed command may only know its completion
    # time.  Treat that as a zero-duration run instead of comparing it with the
    # recorder's current clock, which could incorrectly put the start after the end.
    started_at = started_at or completed_at
    if completed_at < started_at:
        raise EvidenceError("completion cannot precede start")
    ttl_seconds = component_plan["validity_seconds"]
    if command != component_plan["command"]:
        raise EvidenceError("evidence command does not match the verification plan")
    normalized_environment = validate_environment_fingerprint(execution_environment)
    if not environment_matches_plan(
        normalized_environment,
        component_plan["environment"],
        allow_hosted_runner_drift=allow_hosted_runner_drift,
    ):
        raise EvidenceError("executing component environment does not match the verification plan")
    normalized_counts = _evidence_counts(
        plan=plan,
        component=component,
        artifacts=artifacts,
        screenshot=screenshot,
    )
    if machine_output is None:
        raise EvidenceError("evidence requires digest-bound machine output")
    normalized_output = _validate_machine_output_claim(
        machine_output,
        component=component,
        artifacts=artifacts,
    )
    normalized_counts.update(normalized_output["counts"])
    for key, value in (counts or {}).items():
        if key in normalized_counts and normalized_counts[key] != value:
            raise EvidenceError("caller-supplied evidence counts contradict the plan")
        normalized_counts[key] = value
    normalized_counts = dict(sorted(normalized_counts.items()))
    normalized_exit_code = (0 if result == "success" else 1) if exit_code is None else exit_code
    envelope = {
        "artifacts": [dict(item) for item in artifacts],
        "command": command,
        "component": component,
        "counts": normalized_counts,
        "digest_algorithm": "sha256",
        "environment": normalized_environment,
        "exit_code": normalized_exit_code,
        "expires_at": isoformat(completed_at + timedelta(seconds=ttl_seconds)),
        "input_manifest": component_plan["inputs"]["manifest"],
        "input_sha256": component_plan["inputs"]["aggregate_sha256"],
        "origin": dict(origin),
        "output": normalized_output,
        "policy": {
            "graph_sha256": plan["graph_sha256"],
            "policy_version": plan["policy_version"],
        },
        "produced_at": isoformat(completed_at),
        "result": result,
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "screenshot": dict(screenshot) if screenshot is not None else None,
        "selection": {
            "direct_nodes": plan["direct_nodes"],
            "downstream_nodes": plan["downstream_nodes"],
            "reason": component_plan["reason"],
            "risk_flags": plan["risk_flags"],
        },
        "source_tree": plan["source_tree"],
        "started_at": isoformat(started_at),
        "supersedes": supersedes,
        "validity_class": component_plan["validity_class"],
        "validity_seconds": ttl_seconds,
    }
    envelope["evidence_id"] = sha256_json(envelope)
    return validate_envelope(envelope)


def validate_envelope(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise EvidenceError("evidence envelope must be an object")
    expected = {
        "artifacts",
        "command",
        "component",
        "counts",
        "digest_algorithm",
        "environment",
        "evidence_id",
        "exit_code",
        "expires_at",
        "input_manifest",
        "input_sha256",
        "origin",
        "output",
        "policy",
        "produced_at",
        "result",
        "schema_version",
        "screenshot",
        "selection",
        "source_tree",
        "started_at",
        "supersedes",
        "validity_class",
        "validity_seconds",
    }
    if set(payload) != expected or payload["schema_version"] != EVIDENCE_SCHEMA_VERSION:
        raise EvidenceError("evidence envelope has an unsupported shape or schema")
    for field in ("component", "command", "evidence_id", "validity_class"):
        if not isinstance(payload[field], str) or not payload[field]:
            raise EvidenceError(f"{field} must be a non-empty string")
    if not SHA256_RE.fullmatch(payload["evidence_id"]):
        raise EvidenceError("evidence_id must be a full SHA-256 digest")
    identity_payload = dict(payload)
    identity_payload.pop("evidence_id")
    if payload["evidence_id"] != sha256_json(identity_payload):
        raise EvidenceError("evidence_id does not match the canonical envelope content")
    if payload["digest_algorithm"] != "sha256":
        raise EvidenceError("evidence digest algorithm is unsupported")
    if payload["component"] not in ALLOWED_COMPONENT_COMMANDS:
        raise EvidenceError("evidence component is unsupported")
    if payload["command"] not in ALLOWED_COMPONENT_COMMANDS[payload["component"]]:
        raise EvidenceError("evidence command is not allowlisted for its component")
    if not isinstance(payload["result"], str) or payload["result"] not in RESULTS:
        raise EvidenceError("evidence result is unsupported")
    if (
        not isinstance(payload["exit_code"], int)
        or isinstance(payload["exit_code"], bool)
        or payload["exit_code"] < 0
        or (payload["result"] == "success") != (payload["exit_code"] == 0)
    ):
        raise EvidenceError("evidence exit status contradicts its result")
    if not isinstance(payload["input_sha256"], str) or not SHA256_RE.fullmatch(
        payload["input_sha256"]
    ):
        raise EvidenceError("input digest must be a full lowercase SHA-256")
    if digest_manifest(payload["input_manifest"]) != payload["input_sha256"]:
        raise EvidenceError("input manifest digest does not match")
    produced = parse_time(payload["produced_at"], "produced_at")
    started = parse_time(payload["started_at"], "started_at")
    expires = parse_time(payload["expires_at"], "expires_at")
    validity_class = payload["validity_class"]
    validity_seconds = payload["validity_seconds"]
    if (
        validity_class not in VALIDITY_SECONDS
        or not isinstance(validity_seconds, int)
        or isinstance(validity_seconds, bool)
        or validity_seconds != VALIDITY_SECONDS[validity_class]
        or expires != produced + timedelta(seconds=validity_seconds)
        or started > produced
    ):
        raise EvidenceError("evidence timestamps are inconsistent")
    _validate_environment(payload["environment"])
    _validate_source_tree(payload["source_tree"])
    _validate_policy(payload["policy"])
    _validate_origin(payload["origin"])
    _validate_selection(payload["selection"])
    if (
        not isinstance(payload["counts"], dict)
        or not payload["counts"]
        or any(
            not isinstance(key, str)
            or not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
            for key, value in payload["counts"].items()
        )
    ):
        raise EvidenceError("counts must be non-negative integer values")
    _validate_artifacts(payload["artifacts"])
    if not payload["artifacts"]:
        raise EvidenceError("evidence must contain at least one artifact")
    _validate_machine_output_claim(
        payload["output"],
        component=payload["component"],
        artifacts=payload["artifacts"],
    )
    if any(
        payload["counts"].get(key) != value for key, value in payload["output"]["counts"].items()
    ):
        raise EvidenceError("evidence counts do not match the machine output claim")
    _validate_counts(
        payload["counts"],
        component=payload["component"],
        artifact_count=len(payload["artifacts"]),
        result=payload["result"],
        screenshot=payload["screenshot"],
    )
    if payload["supersedes"] is not None and (
        not isinstance(payload["supersedes"], str) or not SHA256_RE.fullmatch(payload["supersedes"])
    ):
        raise EvidenceError("supersedes must be a full evidence id or null")
    _validate_screenshot(
        payload["screenshot"], component=payload["component"], artifacts=payload["artifacts"]
    )
    return payload


def _evidence_counts(
    *,
    plan: Mapping[str, Any],
    component: str,
    artifacts: Sequence[Mapping[str, Any]],
    screenshot: Mapping[str, Any] | None,
) -> dict[str, int]:
    counts = {
        "artifacts": len(artifacts),
        "commands": 1,
        "input_files": len(plan["components"][component]["inputs"]["manifest"]),
    }
    if component == "selector":
        counts |= {
            "changed_paths": len(plan["changed_paths"]),
            "direct_nodes": len(plan["direct_nodes"]),
            "downstream_nodes": len(plan["downstream_nodes"]),
        }
    elif component == "django":
        counts["selected_test_labels"] = len(plan["test_labels"])
    elif component == "content_invariants":
        counts["structured_files"] = len(plan["large_content"]["paths"])
    elif component == "screenshots":
        captures = screenshot.get("captures", []) if isinstance(screenshot, Mapping) else []
        counts["assertions"] = len(captures)
        counts["captures"] = len(captures)
    return counts


def _validate_counts(
    value: Mapping[str, int],
    *,
    component: str,
    artifact_count: int,
    result: str,
    screenshot: object,
) -> None:
    required = {
        "artifacts",
        "assertions",
        "commands",
        "failed",
        "input_files",
        "passed",
        "skipped",
        "tests",
    }
    component_required = {
        "compatibility": set(),
        "container": set(),
        "content_invariants": {"records", "structured_files"},
        "django": {"selected_test_labels"},
        "evidence_validation": set(),
        "playwright": set(),
        "quality": set(),
        "screenshots": {"assertions", "captures"},
        "selector": {"changed_paths", "direct_nodes", "downstream_nodes"},
    }[component]
    expected = required.union(component_required)
    if set(value) != expected:
        raise EvidenceError("evidence counts omit required component metrics")
    if value["artifacts"] != artifact_count or value["commands"] <= 0:
        raise EvidenceError("evidence counts contradict the recorded work")
    for key in ("assertions", "structured_files"):
        if result == "success" and key in value and value[key] <= 0:
            raise EvidenceError("evidence count must prove non-empty verification work")
    if value["tests"] != value["passed"] + value["failed"] + value["skipped"]:
        raise EvidenceError("test outcome counts do not equal the executed test count")
    if result == "success" and value["failed"] != 0:
        raise EvidenceError("successful evidence cannot contain failed outcomes")
    if result == "success" and component in TEST_OUTPUT_COMPONENTS and value["tests"] <= 0:
        raise EvidenceError("test evidence must prove at least one executed test")
    if component == "screenshots":
        captures = screenshot.get("captures") if isinstance(screenshot, Mapping) else None
        if (
            not isinstance(captures, list)
            or value["captures"] != len(captures)
            or value["assertions"] != len(captures)
        ):
            raise EvidenceError("screenshot counts contradict capture evidence")


def machine_output_claim(
    path: str | Path,
    *,
    root: str | Path,
    component: str,
    plan: Mapping[str, Any],
    result: str,
    screenshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if component not in OUTPUT_FORMATS:
        raise EvidenceError("machine output component is unsupported")
    artifact = artifact_records((path,), root=root)[0]
    body = Path(path).read_bytes()
    if result != "success":
        counts = _zero_result_counts()
        output_format = next(iter(OUTPUT_FORMATS[component]))
    elif component in TEST_OUTPUT_COMPONENTS:
        output_format = "test-log-v1"
        counts = _test_output_counts(body)
    elif component == "selector":
        output_format = "verification-plan-v3"
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise EvidenceError("selector machine output is malformed") from exc
        if payload != plan:
            raise EvidenceError("selector machine output does not match the plan")
        counts = _zero_result_counts() | {"assertions": 1}
    elif component == "content_invariants":
        output_format = "content-invariants-v1"
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise EvidenceError("content invariant machine output is malformed") from exc
        if (
            not isinstance(payload, dict)
            or payload.get("status") != "pass"
            or payload.get("input_sha256") != plan["large_content"]["sha256"]
            or not isinstance(payload.get("path_count"), int)
            or not isinstance(payload.get("record_count"), int)
            or payload["path_count"] <= 0
            or payload["record_count"] <= 0
        ):
            raise EvidenceError("content invariant machine output is incomplete")
        counts = _zero_result_counts() | {
            "assertions": payload["record_count"] * 3 + payload["path_count"],
            "records": payload["record_count"],
            "structured_files": payload["path_count"],
        }
    elif component == "container":
        output_format = "container-check-v1"
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise EvidenceError("container machine output is malformed") from exc
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != 1
            or payload.get("status") != "pass"
            or not isinstance(payload.get("assertions"), list)
            or not payload["assertions"]
            or any(not isinstance(item, str) or not item for item in payload["assertions"])
            or payload.get("revision") != plan["head"]
        ):
            raise EvidenceError("container machine output is incomplete")
        counts = _zero_result_counts() | {"assertions": len(payload["assertions"])}
    else:
        output_format = "screenshot-artifact-v1"
        captures = screenshot.get("captures", []) if isinstance(screenshot, Mapping) else []
        if not captures:
            raise EvidenceError("screenshot machine output is incomplete")
        counts = _zero_result_counts() | {"assertions": len(captures), "captures": len(captures)}
    return {"artifact": artifact, "counts": counts, "format": output_format}


def validate_machine_output_files(
    envelope: Mapping[str, Any],
    *,
    evidence_root: Path,
    envelope_path: Path,
    plan: Mapping[str, Any],
) -> None:
    output = envelope["output"]
    artifact = output["artifact"]
    path = _find_artifact(artifact, evidence_root=evidence_root, envelope_path=envelope_path)
    if envelope["component"] == "content_invariants" and envelope["result"] == "success":
        from ci.content_invariants import validate_invariant_artifact

        try:
            content_payload = json.loads(path.read_text(encoding="utf-8"))
            validate_invariant_artifact(content_payload, plan=plan)
        except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise EvidenceError("machine_output_mismatch") from exc
    expected = machine_output_claim(
        path,
        root=path.parent,
        component=envelope["component"],
        plan=plan,
        result=envelope["result"],
        screenshot=envelope["screenshot"],
    )
    # The helper uses a path relative to its immediate parent. Normalize that
    # location-only value before comparing the content-bound claim.
    expected["artifact"]["path"] = artifact["path"]
    if expected != output:
        raise EvidenceError("machine_output_mismatch")


def _validate_machine_output_claim(
    value: object,
    *,
    component: str,
    artifacts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"artifact", "counts", "format"}:
        raise EvidenceError("machine output claim has an invalid shape")
    if value["format"] not in OUTPUT_FORMATS[component]:
        raise EvidenceError("machine output format does not match its component")
    _validate_artifacts([value["artifact"]])
    if value["artifact"] not in artifacts:
        raise EvidenceError("machine output is not bound to an evidence artifact")
    counts = value["counts"]
    required = {"assertions", "failed", "passed", "skipped", "tests"}
    extra = {
        "content-invariants-v1": {"records", "structured_files"},
        "screenshot-artifact-v1": {"captures"},
    }.get(value["format"], set())
    if (
        not isinstance(counts, dict)
        or set(counts) != required.union(extra)
        or any(
            not isinstance(key, str)
            or not isinstance(item, int)
            or isinstance(item, bool)
            or item < 0
            for key, item in counts.items()
        )
    ):
        raise EvidenceError("machine output counts are invalid")
    return {"artifact": dict(value["artifact"]), "counts": dict(counts), "format": value["format"]}


def _test_output_counts(body: bytes) -> dict[str, int]:
    try:
        text = body.decode("utf-8")
    except UnicodeError as exc:
        raise EvidenceError("test output must be UTF-8") from exc
    passed = failed = skipped = 0
    for line in text.splitlines():
        if not PYTEST_DURATION_RE.search(line):
            continue
        outcomes = list(PYTEST_OUTCOME_RE.finditer(line))
        for match in outcomes:
            count = int(match.group("count"))
            outcome = match.group("outcome")
            if outcome in {"passed", "xpassed"}:
                passed += count
            elif outcome in {"failed", "error", "errors"}:
                failed += count
            else:
                skipped += count
    unittest_runs = [int(match.group("count")) for match in UNITTEST_RAN_RE.finditer(text)]
    if unittest_runs:
        unittest_skips = sum(
            int(match.group("count")) for match in UNITTEST_SKIPPED_RE.finditer(text)
        )
        unittest_failures = sum(
            int(match.group("count")) for match in UNITTEST_FAILURE_RE.finditer(text)
        )
        unittest_total = sum(unittest_runs)
        if unittest_skips + unittest_failures > unittest_total:
            raise EvidenceError("unittest output has contradictory skip counts")
        # A successful command's Django unittest output ends in OK. Failure
        # output is retained but is never reusable; its non-zero result is the
        # authoritative outcome.
        passed += unittest_total - unittest_skips - unittest_failures
        failed += unittest_failures
        skipped += unittest_skips
    tests = passed + failed + skipped
    if tests <= 0:
        raise EvidenceError("test output contains no machine-verifiable outcome counts")
    return {
        "assertions": tests,
        "failed": failed,
        "passed": passed,
        "skipped": skipped,
        "tests": tests,
    }


def _zero_result_counts() -> dict[str, int]:
    return {"assertions": 0, "failed": 0, "passed": 0, "skipped": 0, "tests": 0}


def load_envelopes(directory: str | Path) -> list[tuple[Path, dict[str, Any]]]:
    root = Path(directory)
    if not root.exists():
        return []
    files = sorted(path for path in root.rglob("*.json") if path.is_file())
    if len(files) > MAX_EVIDENCE_FILES:
        raise EvidenceError("evidence directory contains too many JSON files")
    envelopes: list[tuple[Path, dict[str, Any]]] = []
    for path in files:
        if path.stat().st_size > MAX_EVIDENCE_BYTES:
            raise EvidenceError("evidence JSON file is too large")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            envelope = validate_envelope(payload)
        except (json.JSONDecodeError, UnicodeError, EvidenceError):
            continue
        envelopes.append((path, envelope))
    return envelopes


def choose_reusable_evidence(
    *,
    plan: Mapping[str, Any],
    component: str,
    candidates: Sequence[tuple[Path, Mapping[str, Any]]],
    evidence_root: str | Path,
    consumer: str,
    now: datetime | None = None,
    history: Sequence[Mapping[str, Any]] = (),
) -> tuple[dict[str, Any] | None, str]:
    now = now or utc_now()
    component_plan = plan["components"][component]
    matching: list[tuple[Path, dict[str, Any]]] = []
    component_candidates: list[tuple[Path, dict[str, Any]]] = []
    invalid_reason = "no_matching_evidence"
    for path, raw in candidates:
        try:
            envelope = validate_envelope(dict(raw))
        except EvidenceError:
            continue
        if envelope["component"] != component:
            continue
        component_candidates.append((path, envelope))
        if envelope["input_sha256"] != component_plan["inputs"]["aggregate_sha256"]:
            invalid_reason = "relevant_inputs_changed"
            continue
        if envelope["environment"] != component_plan["environment"]:
            invalid_reason = "environment_changed"
            continue
        if envelope["policy"] != {
            "graph_sha256": plan["graph_sha256"],
            "policy_version": plan["policy_version"],
        }:
            invalid_reason = "policy_changed"
            continue
        if (
            envelope["command"] != component_plan["command"]
            or envelope["validity_class"] != component_plan["validity_class"]
            or envelope["validity_seconds"] != component_plan["validity_seconds"]
        ):
            invalid_reason = "component_contract_changed"
            continue
        matching.append((path, envelope))
    if not matching:
        return None, invalid_reason
    matching.sort(key=lambda item: parse_time(item[1]["produced_at"], "produced_at"), reverse=True)
    path, newest = matching[0]
    newest_time = parse_time(newest["produced_at"], "produced_at")
    for _candidate_path, candidate in component_candidates:
        if parse_time(candidate["produced_at"], "produced_at") <= newest_time:
            continue
        if candidate["supersedes"] == newest["evidence_id"]:
            return None, "superseded"
        if candidate["result"] != "success":
            return None, f"latest_result_{candidate['result']}"
    if newest["result"] != "success":
        return None, f"latest_result_{newest['result']}"
    if parse_time(newest["expires_at"], "expires_at") <= now:
        return None, "expired"
    try:
        validate_origin_trust(newest["origin"], consumer=consumer, plan=plan, component=component)
        validate_artifact_files(
            newest["artifacts"], evidence_root=Path(evidence_root), envelope_path=path
        )
        validate_machine_output_files(
            newest,
            evidence_root=Path(evidence_root),
            envelope_path=path,
            plan=plan,
        )
    except EvidenceError as exc:
        return None, str(exc)
    for run in history:
        if _history_invalidates(run, newest):
            return None, "later_non_successful_run"
    if component == "screenshots":
        try:
            validate_screenshot_for_plan(newest, plan=plan)
        except EvidenceError as exc:
            return None, str(exc)
    return newest, "exact_digest_match"


def artifact_records(paths: Iterable[str | Path], *, root: str | Path) -> list[dict[str, Any]]:
    root = Path(root).resolve()
    records: list[dict[str, Any]] = []
    for value in paths:
        path = Path(value).resolve()
        try:
            relative = path.relative_to(root)
        except ValueError as exc:
            raise EvidenceError("artifact must be below the declared evidence root") from exc
        if not path.is_file() or path.is_symlink():
            raise EvidenceError("artifact must be a regular file")
        records.append(
            {
                "path": relative.as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size": path.stat().st_size,
            }
        )
    return sorted(records, key=lambda item: item["path"])


def validate_environment_fingerprint(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "allowlisted_config",
        "architecture",
        "browser",
        "database",
        "django",
        "operating_system",
        "playwright",
        "python",
        "runner_image",
        "runner_image_version",
        "sha256",
        "uv",
    }:
        raise EvidenceError("environment fingerprint has an invalid shape")
    digest_payload = dict(value)
    digest = digest_payload.pop("sha256")
    if not isinstance(digest, str) or digest != sha256_json(digest_payload):
        raise EvidenceError("environment fingerprint digest does not match")
    config = value["allowlisted_config"]
    if (
        not isinstance(config, dict)
        or set(config) - set(ALLOWLISTED_CONFIG)
        or any(not isinstance(item, str) for item in config.values())
    ):
        raise EvidenceError("environment includes non-allowlisted configuration")
    for field in set(value) - {"allowlisted_config", "sha256"}:
        if not isinstance(value[field], str) or not value[field]:
            raise EvidenceError("environment fingerprint values must be non-empty strings")
    return dict(value)


def _validate_environment(value: object) -> None:
    validate_environment_fingerprint(value)


def _validate_source_tree(value: object) -> None:
    if not isinstance(value, dict) or set(value) != {
        "commit",
        "entry_count",
        "git_object_algorithm",
        "manifest_sha256",
        "tree_oid",
    }:
        raise EvidenceError("source tree has an invalid shape")
    if any(
        not isinstance(value[field], str) or not SHA_RE.fullmatch(value[field])
        for field in ("commit", "tree_oid")
    ):
        raise EvidenceError("source tree Git ids are invalid")
    if not isinstance(value["git_object_algorithm"], str) or value["git_object_algorithm"] not in {
        "sha1",
        "sha256",
    }:
        raise EvidenceError("source tree Git object algorithm is invalid")
    if not isinstance(value["manifest_sha256"], str) or not SHA256_RE.fullmatch(
        value["manifest_sha256"]
    ):
        raise EvidenceError("source manifest digest is invalid")
    if (
        not isinstance(value["entry_count"], int)
        or isinstance(value["entry_count"], bool)
        or value["entry_count"] < 0
    ):
        raise EvidenceError("source entry count is invalid")


def _validate_policy(value: object) -> None:
    if not isinstance(value, dict) or set(value) != {"graph_sha256", "policy_version"}:
        raise EvidenceError("policy reference has an invalid shape")
    if not isinstance(value["graph_sha256"], str) or not SHA256_RE.fullmatch(value["graph_sha256"]):
        raise EvidenceError("graph digest is invalid")
    if not isinstance(value["policy_version"], int) or isinstance(value["policy_version"], bool):
        raise EvidenceError("policy version is invalid")


def _validate_origin(value: object) -> None:
    if not isinstance(value, dict):
        raise EvidenceError("evidence origin is invalid")
    kind = value.get("kind")
    if not isinstance(kind, str) or kind not in {"github_actions", "local"}:
        raise EvidenceError("evidence origin is invalid")
    if value["kind"] == "github_actions":
        expected = {
            "artifact_id",
            "job_id",
            "kind",
            "ref",
            "repository",
            "run_attempt",
            "run_id",
            "workflow",
        }
        if set(value) != expected:
            raise EvidenceError("GitHub Actions origin has an invalid shape")
        for field in ("run_attempt", "run_id"):
            if (
                not isinstance(value[field], int)
                or isinstance(value[field], bool)
                or value[field] <= 0
            ):
                raise EvidenceError("GitHub Actions origin IDs must be positive integers")
        for field in ("artifact_id", "job_id", "ref", "repository", "workflow"):
            if not isinstance(value[field], str) or not value[field]:
                raise EvidenceError("GitHub Actions origin strings must be non-empty")
    else:
        expected = {"issue", "kind", "producer_role", "worktree"}
        if (
            set(value) != expected
            or not isinstance(value["producer_role"], str)
            or value["producer_role"] not in {"engineer", "tester"}
        ):
            raise EvidenceError("local evidence origin has an invalid shape")
        if (
            not isinstance(value["issue"], int)
            or isinstance(value["issue"], bool)
            or value["issue"] <= 0
        ):
            raise EvidenceError("local evidence issue must be positive")
        if not isinstance(value["worktree"], str) or not value["worktree"]:
            raise EvidenceError("local evidence worktree must be non-empty")


def _validate_selection(value: object) -> None:
    if not isinstance(value, dict) or set(value) != {
        "direct_nodes",
        "downstream_nodes",
        "reason",
        "risk_flags",
    }:
        raise EvidenceError("evidence selection has an invalid shape")
    for field in ("direct_nodes", "downstream_nodes", "risk_flags"):
        values = value[field]
        if (
            not isinstance(values, list)
            or any(not isinstance(item, str) for item in values)
            or values != sorted(set(values))
        ):
            raise EvidenceError("evidence selection lists must be sorted unique strings")
    if not isinstance(value["reason"], str) or not value["reason"]:
        raise EvidenceError("evidence selection reason is invalid")


def _validate_artifacts(value: object) -> None:
    if not isinstance(value, list) or len(value) > 100:
        raise EvidenceError("artifact list is invalid")
    paths: list[str] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "size"}:
            raise EvidenceError("artifact record has an invalid shape")
        _safe_repository_path(item["path"])
        if not isinstance(item["sha256"], str) or not SHA256_RE.fullmatch(item["sha256"]):
            raise EvidenceError("artifact digest is invalid")
        if not isinstance(item["size"], int) or isinstance(item["size"], bool) or item["size"] < 0:
            raise EvidenceError("artifact size is invalid")
        paths.append(item["path"])
    if paths != sorted(set(paths)):
        raise EvidenceError("artifact paths must be sorted and unique")


def _validate_screenshot(
    value: object, *, component: str, artifacts: Sequence[Mapping[str, Any]]
) -> None:
    if value is None:
        if component == "screenshots":
            raise EvidenceError("screenshot component requires capture evidence")
        return
    if (
        component != "screenshots"
        or not isinstance(value, dict)
        or set(value) != {"captures", "reviewer"}
        or not isinstance(value["reviewer"], str)
        or not value["reviewer"]
        or not isinstance(value["captures"], list)
        or not value["captures"]
    ):
        raise EvidenceError("screenshot evidence has an invalid shape")
    artifacts_by_path = {item["path"]: item for item in artifacts}
    identities: set[tuple[object, ...]] = set()
    for capture in value["captures"]:
        expected = {
            "artifact_path",
            "browser",
            "captured_at",
            "height",
            "image_sha256",
            "independent_inspection",
            "render_sha256",
            "route",
            "route_state",
            "verdict",
            "viewport",
            "width",
        }
        if not isinstance(capture, dict) or set(capture) != expected:
            raise EvidenceError("screenshot capture has an invalid shape")
        if capture["verdict"] != "pass" or capture["independent_inspection"] is not True:
            raise EvidenceError("screenshot evidence requires independent pass inspection")
        for field in (
            "artifact_path",
            "browser",
            "route",
            "route_state",
            "viewport",
        ):
            if not isinstance(capture[field], str) or not capture[field]:
                raise EvidenceError("screenshot identity fields must be non-empty")
        if not capture["route"].startswith("/"):
            raise EvidenceError("screenshot route must be repository-relative HTTP path")
        for field in ("height", "width"):
            if (
                not isinstance(capture[field], int)
                or isinstance(capture[field], bool)
                or capture[field] <= 0
            ):
                raise EvidenceError("screenshot viewport dimensions must be positive integers")
        parse_time(capture["captured_at"], "screenshot captured_at")
        if not isinstance(capture["render_sha256"], str) or not SHA256_RE.fullmatch(
            capture["render_sha256"]
        ):
            raise EvidenceError("screenshot render digest is invalid")
        if not isinstance(capture["image_sha256"], str) or not SHA256_RE.fullmatch(
            capture["image_sha256"]
        ):
            raise EvidenceError("screenshot image digest is invalid")
        artifact = artifacts_by_path.get(capture["artifact_path"])
        if artifact is None or artifact["sha256"] != capture["image_sha256"]:
            raise EvidenceError("screenshot image is not bound to an artifact digest")
        identity = (
            capture["browser"],
            capture["route"],
            capture["route_state"],
            capture["viewport"],
            capture["width"],
            capture["height"],
        )
        if identity in identities:
            raise EvidenceError("screenshot coverage identity is duplicated")
        identities.add(identity)


def validate_screenshot_for_plan(envelope: Mapping[str, Any], *, plan: Mapping[str, Any]) -> None:
    screenshot = envelope.get("screenshot")
    if not isinstance(screenshot, dict):
        raise EvidenceError("screenshot_not_independently_inspected")
    captures = screenshot["captures"]
    expected = {
        (
            item["browser"],
            item["route"],
            item["route_state"],
            item["viewport"],
            item["width"],
            item["height"],
        )
        for item in plan["render"]["required_captures"]
    }
    actual = {
        (
            item["browser"],
            item["route"],
            item["route_state"],
            item["viewport"],
            item["width"],
            item["height"],
        )
        for item in captures
    }
    if actual != expected:
        raise EvidenceError("screenshot_coverage_incomplete")
    if any(item["render_sha256"] != plan["render"]["sha256"] for item in captures):
        raise EvidenceError("render_inputs_changed")


def validate_origin_trust(
    origin: Mapping[str, Any],
    *,
    consumer: str,
    plan: Mapping[str, Any],
    component: str,
) -> None:
    if consumer == "ci":
        if origin["kind"] != "github_actions":
            raise EvidenceError("untrusted_origin")
        _validate_ci_origin(origin, plan=plan, component=component)
    elif consumer == "tester" and origin["kind"] == "github_actions":
        _validate_ci_origin(origin, plan=plan, component=component)
    elif consumer == "tester":
        if origin["kind"] != "local" or origin["producer_role"] != "tester":
            raise EvidenceError("untrusted_origin")
    elif consumer == "engineer":
        return
    else:
        raise EvidenceError("unknown_consumer")


def _validate_ci_origin(
    origin: Mapping[str, Any], *, plan: Mapping[str, Any], component: str
) -> None:
    if origin["kind"] != "github_actions":
        raise EvidenceError("untrusted_origin")
    if origin["repository"] != plan["repository"] or origin["workflow"] not in {
        ".github/workflows/ci.yml",
        ".github/workflows/scheduled-full-regression.yml",
    }:
        raise EvidenceError("untrusted_origin")
    if origin["ref"] not in {"refs/heads/main", "main"}:
        raise EvidenceError("untrusted_origin")
    allowed_components = TRUSTED_CI_JOB_COMPONENTS.get(origin["job_id"])
    if allowed_components is None or component not in allowed_components:
        raise EvidenceError("untrusted_origin")
    expected_artifact = _expected_ci_artifact(origin["job_id"], origin)
    if origin["artifact_id"] != expected_artifact:
        raise EvidenceError("untrusted_origin")


def validate_artifact_files(
    artifacts: Sequence[Mapping[str, Any]], *, evidence_root: Path, envelope_path: Path
) -> None:
    if not artifacts:
        raise EvidenceError("missing_artifact")
    for item in artifacts:
        found = _find_artifact(item, evidence_root=evidence_root, envelope_path=envelope_path)
        body = found.read_bytes()
        if len(body) != item["size"] or hashlib.sha256(body).hexdigest() != item["sha256"]:
            raise EvidenceError("artifact_digest_mismatch")


def _find_artifact(item: Mapping[str, Any], *, evidence_root: Path, envelope_path: Path) -> Path:
    candidates = (evidence_root.resolve(), envelope_path.parent.resolve())
    for root in candidates:
        path = (root / item["path"]).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            continue
        if path.is_file() and not path.is_symlink():
            return path
    raise EvidenceError("missing_artifact")


def _expected_ci_artifact(job_id: str, origin: Mapping[str, Any]) -> str:
    run_id = origin["run_id"]
    attempt = origin["run_attempt"]
    if job_id == "classification":
        return f"ci-selection-{run_id}-attempt-{attempt}"
    return f"verification-component-{job_id}-{run_id}-attempt-{attempt}"


def _history_invalidates(run: Mapping[str, Any], envelope: Mapping[str, Any]) -> bool:
    if run.get("conclusion") == "success":
        return False
    run_id = run.get("id")
    if not isinstance(run_id, int) or isinstance(run_id, bool):
        return True
    origin = envelope["origin"]
    return origin["kind"] == "github_actions" and run_id > origin["run_id"]


def _safe_repository_path(path: object) -> str:
    if not isinstance(path, str):
        raise EvidenceError("path must be a string")
    parts = path.split("/")
    if (
        not path
        or path.startswith("/")
        or "\x00" in path
        or all(part == "" for part in parts)
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise EvidenceError("path is unsafe")
    return path


def _git(repository: Path, *arguments: str) -> bytes:
    try:
        return subprocess.run(
            ["git", "-C", os.fspath(repository), *arguments],
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise EvidenceError("Git manifest command failed") from exc


def _package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unavailable"


def _uv_version() -> str:
    try:
        output = subprocess.run(
            ["uv", "--version"], check=True, capture_output=True, text=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"
    return output.strip().removeprefix("uv ") or "unavailable"
