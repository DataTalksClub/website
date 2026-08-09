from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
SHA_RE = re.compile(r"^[0-9a-f]{40}$")

APPLICATION_TEST_LABELS: dict[str, tuple[str, ...]] = {
    "api": ("api",),
    "cadmin": ("cadmin",),
    "content": ("accounts", "content.tests", "core"),
    "courses": ("accounts", "api", "cadmin", "content.tests", "core", "courses", "data"),
    "data": ("api", "cadmin", "courses", "data"),
    "jobs": ("jobs",),
    "management_api": ("api", "management_api"),
    "management_auth": ("api", "core", "management_api", "management_auth"),
    "review_import": ("accounts", "review_import"),
    "studio": ("accounts", "core", "studio"),
}
TEST_LABEL_ALLOWLIST = frozenset(
    label for labels in APPLICATION_TEST_LABELS.values() for label in labels
)

FULL_REASONS = frozenset(
    {
        "base_invalid",
        "base_unavailable",
        "base_zero",
        "configuration_or_dependency",
        "cross_application",
        "diff_empty",
        "diff_failed",
        "diff_unparseable",
        "documentation_or_contract",
        "head_invalid",
        "head_mismatch",
        "head_unavailable",
        "html_changed",
        "manual_dispatch",
        "migration_changed",
        "non_ancestor_base",
        "static_changed",
        "shared_application",
        "template_changed",
        "testless_application",
        "unknown_path",
        "unsupported_file_mode",
        "unsupported_status",
    }
)
REASONS = FULL_REASONS | {"single_application"}


class DiffParseError(ValueError):
    """The Git name-status stream is not structurally valid."""


class UnsupportedStatus(ValueError):
    """The Git name-status stream contains a change kind we do not focus."""


@dataclass(frozen=True)
class ChangeRecord:
    status: str
    paths: tuple[str, ...]
    ordinary: bool = True


def parse_name_status(data: bytes) -> tuple[ChangeRecord, ...]:
    """Parse ``git diff --name-status -z`` without line or shell splitting."""
    if not data or not data.endswith(b"\0"):
        raise DiffParseError("name-status output must be non-empty and NUL terminated")
    fields = data[:-1].split(b"\0")
    records: list[ChangeRecord] = []
    index = 0
    while index < len(fields):
        status_bytes = fields[index]
        index += 1
        try:
            status = status_bytes.decode("ascii")
        except UnicodeDecodeError as exc:
            raise DiffParseError("status is not ASCII") from exc

        path_count = 1
        if status.startswith(("R", "C")):
            score = status[1:]
            if len(score) != 3 or not score.isdigit() or not 0 <= int(score) <= 100:
                raise UnsupportedStatus(status)
            path_count = 2
        elif status not in {"A", "M", "D"}:
            raise UnsupportedStatus(status)

        if index + path_count > len(fields):
            raise DiffParseError("status record has missing paths")
        raw_paths = fields[index : index + path_count]
        index += path_count
        if any(not path for path in raw_paths):
            raise DiffParseError("status record contains an empty path")
        paths = tuple(path.decode("utf-8", errors="surrogateescape") for path in raw_paths)
        records.append(ChangeRecord(status=status, paths=paths))
    return tuple(records)


def full_selection(
    *,
    event: str,
    base: str | None,
    head: str | None,
    reason: str,
    changed_path_count: int = 0,
    application_roots: tuple[str, ...] = (),
) -> dict[str, Any]:
    if reason not in FULL_REASONS:
        raise ValueError(f"unsupported full-selection reason: {reason}")
    return {
        "application_roots": list(sorted(set(application_roots))),
        "base": base,
        "changed_path_count": changed_path_count,
        "event": event,
        "head": head,
        "profile": "full",
        "reason": reason,
        "schema_version": SCHEMA_VERSION,
        "test_labels": [],
    }


def classify_records(
    records: tuple[ChangeRecord, ...],
    *,
    event: str,
    base: str,
    head: str,
) -> dict[str, Any]:
    paths = tuple(path for record in records for path in record.paths)
    mapped_roots = {root for path in paths if (root := _mapped_root(path)) is not None}
    roots = tuple(sorted(mapped_roots))

    def full(reason: str) -> dict[str, Any]:
        return full_selection(
            event=event,
            base=base,
            head=head,
            reason=reason,
            changed_path_count=len(paths),
            application_roots=roots,
        )

    if not records:
        return full("diff_empty")
    if any(not record.ordinary for record in records):
        return full("unsupported_file_mode")
    if any(not _valid_repository_path(path) for path in paths):
        return full("unknown_path")

    force_reason = _force_full_reason(paths)
    if force_reason:
        return full(force_reason)
    if any(_mapped_root(path) is None for path in paths):
        return full("unknown_path")
    if len(roots) != 1:
        return full("cross_application")

    root = roots[0]
    labels = APPLICATION_TEST_LABELS[root]
    selection = {
        "application_roots": [root],
        "base": base,
        "changed_path_count": len(paths),
        "event": event,
        "head": head,
        "profile": "focused",
        "reason": "single_application",
        "schema_version": SCHEMA_VERSION,
        "test_labels": list(labels),
    }
    validate_selection(selection)
    return selection


def validate_selection(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("selection must be a JSON object")
    expected_keys = {
        "application_roots",
        "base",
        "changed_path_count",
        "event",
        "head",
        "profile",
        "reason",
        "schema_version",
        "test_labels",
    }
    if set(payload) != expected_keys:
        raise ValueError("selection has unexpected or missing fields")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise ValueError("unsupported selection schema")
    if payload["event"] not in {"push", "workflow_dispatch"}:
        raise ValueError("unsupported selection event")
    if payload["profile"] not in {"focused", "full"}:
        raise ValueError("unsupported selection profile")
    if payload["reason"] not in REASONS:
        raise ValueError("unsupported selection reason")
    if not isinstance(payload["changed_path_count"], int) or payload["changed_path_count"] < 0:
        raise ValueError("changed_path_count must be a non-negative integer")

    base = payload["base"]
    head = payload["head"]
    if base is not None and (not isinstance(base, str) or not SHA_RE.fullmatch(base)):
        raise ValueError("base must be a lowercase full SHA or null")
    if head is not None and (not isinstance(head, str) or not SHA_RE.fullmatch(head)):
        raise ValueError("head must be a lowercase full SHA or null")

    roots = payload["application_roots"]
    labels = payload["test_labels"]
    if (
        not isinstance(roots, list)
        or any(not isinstance(item, str) for item in roots)
        or roots != sorted(set(roots))
        or any(root not in APPLICATION_TEST_LABELS for root in roots)
    ):
        raise ValueError("application_roots must be sorted, unique, and mapped")
    if (
        not isinstance(labels, list)
        or any(not isinstance(item, str) for item in labels)
        or labels != sorted(set(labels))
        or any(label not in TEST_LABEL_ALLOWLIST for label in labels)
    ):
        raise ValueError("test_labels must be sorted, unique, and allowlisted")

    if payload["profile"] == "focused":
        if payload["event"] != "push":
            raise ValueError("only push events may use a focused selection")
        if payload["reason"] != "single_application" or len(roots) != 1:
            raise ValueError("focused selection must identify one mapped application")
        if base is None or head is None:
            raise ValueError("focused selection requires exact base and head SHAs")
        if labels != list(APPLICATION_TEST_LABELS[roots[0]]):
            raise ValueError("focused labels do not match the reviewed closure")
    else:
        if payload["reason"] not in FULL_REASONS:
            raise ValueError("full selection must use a full-suite reason")
        if labels:
            raise ValueError("full selection cannot contain focused labels")
    if payload["event"] == "workflow_dispatch" and (
        payload["profile"] != "full"
        or payload["reason"] != "manual_dispatch"
        or payload["base"] is not None
    ):
        raise ValueError(
            "manual dispatch must be an explicit full selection without a guessed base"
        )
    if payload["event"] == "push" and payload["reason"] == "manual_dispatch":
        raise ValueError("push selection cannot use the manual-dispatch reason")
    return payload


def load_selection(path: str | Path) -> dict[str, Any]:
    return validate_selection(json.loads(Path(path).read_text(encoding="utf-8")))


def dump_selection(payload: object, path: str | Path) -> None:
    validated = validate_selection(payload)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(validated, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def selection_summary(payload: object) -> str:
    selection = validate_selection(payload)
    roots = ", ".join(selection["application_roots"]) or "none"
    labels = ", ".join(selection["test_labels"]) or "full suite"
    return "\n".join(
        (
            "## Django CI selection",
            "",
            f"- Event: `{selection['event']}`",
            f"- Base: `{selection['base'] or 'unavailable'}`",
            f"- Head: `{selection['head'] or 'unavailable'}`",
            f"- Profile: `{selection['profile']}`",
            f"- Reason: `{selection['reason']}`",
            f"- Application roots: `{roots}`",
            f"- Changed paths: `{selection['changed_path_count']}`",
            f"- Django labels: `{labels}`",
            "",
        )
    )


def _mapped_root(path: str) -> str | None:
    if "/" not in path:
        return None
    root, remainder = path.split("/", 1)
    if not remainder or root not in APPLICATION_TEST_LABELS:
        return None
    return root


def _valid_repository_path(path: str) -> bool:
    parts = path.split("/")
    return bool(
        path and not path.startswith("/") and all(part not in {"", ".", ".."} for part in parts)
    )


def _force_full_reason(paths: tuple[str, ...]) -> str | None:
    components = [path.split("/") for path in paths]
    if any("migrations" in parts for parts in components):
        return "migration_changed"
    if any("templates" in parts for parts in components):
        return "template_changed"
    if any("static" in parts for parts in components):
        return "static_changed"
    template_suffixes = {".htm", ".html", ".jinja", ".jinja2", ".tmpl", ".tpl"}
    if any(Path(path).suffix.lower() in template_suffixes for path in paths):
        return "html_changed"

    roots = {parts[0] for parts in components if parts and parts[0]}
    if roots & {"accounts", "core"}:
        return "shared_application"
    if roots & {"content_sync", "email_app", "events"}:
        return "testless_application"
    if roots & {
        ".claude",
        ".github",
        "course_management",
        "course_platform_templates",
        "deploy",
        "e2e",
        "playwright_tests",
        "scripts",
        "templates",
        "website",
    }:
        return "configuration_or_dependency"
    if roots & {"_docs", "compatibility"} or any(
        path in {"AGENTS.md", "README.md"} for path in paths
    ):
        return "documentation_or_contract"
    if roots & {"ci"} or any(_is_root_configuration(path) for path in paths):
        return "configuration_or_dependency"
    return None


def _is_root_configuration(path: str) -> bool:
    if "/" in path:
        return False
    return (
        path
        in {
            ".python-version",
            "Dockerfile",
            "Makefile",
            "manage.py",
            "pyproject.toml",
            "uv.lock",
        }
        or path.endswith((".lock", ".toml", ".yaml", ".yml"))
        or path
        in {
            "package.json",
            "package-lock.json",
            "Pipfile",
            "tox.ini",
            "pytest.ini",
            "mypy.ini",
        }
        or path.startswith("requirements")
        or path.startswith(".")
    )
