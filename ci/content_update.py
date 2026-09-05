"""Validate the checked content-update projections with a bounded CI contract.

This module is intentionally a projection checker, not a source synchronizer.  The source
repositories build and publish their own sites; this repository checks the immutable projections
that are reviewed into the website.  Reports contain only source identity, file metadata, counts,
and checksums.  They never include projected bodies, answers, URLs, or database data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA_VERSION = 1
CONTRACT_VERSION = "content-update-v1"
FAMILIES = ("courses", "podwiki", "faq", "docs")
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_FILE_COUNT = 4096
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SOURCE_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
CODE_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

# The reviewed source files this check compares upstream against. They are
# ingest input under temporary/content/, not something the site reads.
_PUBLIC_PROJECTION_PATH = "temporary/content/public_projection"
_DECLARED_PROJECTION_PATHS = {
    "courses": (
        f"{_PUBLIC_PROJECTION_PATH}/courses.json",
        f"{_PUBLIC_PROJECTION_PATH}/manifest.json",
    ),
    "podwiki": (
        f"{_PUBLIC_PROJECTION_PATH}/wiki.json",
        f"{_PUBLIC_PROJECTION_PATH}/wiki_graph.json",
        f"{_PUBLIC_PROJECTION_PATH}/wiki_search.json",
        f"{_PUBLIC_PROJECTION_PATH}/manifest.json",
    ),
    "faq": ("temporary/content/faq_projection.json",),
    "docs": ("temporary/content/docs_projection.json",),
}
_ASSET_ROOTS = {
    "courses": (),
    "podwiki": (),
    "faq": ("content/faq_assets",),
    "docs": ("content/docs_assets",),
}
_SOURCE_REPOSITORIES = {
    "courses": "DataTalksClub/course-management-platform",
    "podwiki": "DataTalksClub/podwiki",
    "faq": "DataTalksClub/faq",
    "docs": "DataTalksClub/docs",
}
_PUBLIC_SOURCE_MANIFEST_KEYS = {"courses": "courses", "podwiki": "wiki"}
_CHECK_NAMES = ("checksum", "projection_validation", "redaction", "source_pin")
_CHECK_STATES = frozenset({"pass", "fail", "not_run"})
_REPORT_SENSITIVE_TERMS = (
    "answer",
    "body",
    "cookie",
    "credential",
    "email",
    "enrollment",
    "learner",
    "password",
    "private",
    "registration",
    "secret",
    "session",
    "submission",
    "token",
)
_REPORT_SENSITIVE_VALUE_TERMS = (
    "credential",
    "cookie",
    "email",
    "enrollment",
    "learner",
    "password",
    "private",
    "registration",
    "secret",
    "session",
    "token",
)


class ContentUpdateError(RuntimeError):
    """A content-update check failed with a safe, bounded diagnostic code."""

    def __init__(self, code: str) -> None:
        if not CODE_RE.fullmatch(code):
            code = "content_update_failed"
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class FamilyContract:
    family: str
    source_repository: str
    source_revision: str
    projection_paths: tuple[str, ...]
    asset_roots: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Artifact:
    path: str
    size: int
    sha256: str


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _relative_path(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ContentUpdateError("unsafe_projection_path")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ContentUpdateError("unsafe_projection_path")
    return path


def _reject_symlink_components(path: Path) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        if current.is_symlink():
            raise ContentUpdateError("projection_symlink")


def _regular_file(repository: Path, relative: str) -> tuple[Artifact, bytes]:
    path = _relative_path(relative)
    candidate = repository.joinpath(*path.parts)
    _reject_symlink_components(candidate)
    if candidate.is_symlink() or not candidate.is_file():
        raise ContentUpdateError("projection_file_missing")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(repository)
        content = candidate.read_bytes()
    except (OSError, RuntimeError, ValueError):
        raise ContentUpdateError("projection_file_unreadable") from None
    size = len(content)
    if size > MAX_FILE_BYTES:
        raise ContentUpdateError("projection_file_too_large")
    return Artifact(relative, size, _sha256(content)), content


def _collect_artifacts(repository: Path, contract: FamilyContract) -> list[Artifact]:
    artifacts: list[Artifact] = []
    seen: set[str] = set()
    total_bytes = 0

    def add(relative: str) -> None:
        nonlocal total_bytes
        if relative in seen:
            raise ContentUpdateError("projection_path_duplicate")
        artifact, _content = _regular_file(repository, relative)
        seen.add(relative)
        artifacts.append(artifact)
        total_bytes += artifact.size
        if len(artifacts) > MAX_FILE_COUNT:
            raise ContentUpdateError("projection_file_count_exceeded")
        if total_bytes > MAX_TOTAL_BYTES:
            raise ContentUpdateError("projection_total_size_exceeded")

    for relative in contract.projection_paths:
        add(relative)
    for root_relative in contract.asset_roots:
        root = _relative_path(root_relative)
        root_path = repository.joinpath(*root.parts)
        _reject_symlink_components(root_path)
        if root_path.is_symlink() or not root_path.is_dir():
            raise ContentUpdateError("projection_asset_root_missing")
        try:
            candidates = sorted(root_path.rglob("*"), key=lambda item: item.as_posix())
        except OSError:
            raise ContentUpdateError("projection_asset_root_unreadable") from None
        for candidate in candidates:
            if candidate.is_symlink():
                raise ContentUpdateError("projection_symlink")
            if candidate.is_dir():
                continue
            if not candidate.is_file():
                raise ContentUpdateError("projection_non_regular_file")
            try:
                relative = candidate.relative_to(repository).as_posix()
            except ValueError:
                raise ContentUpdateError("unsafe_projection_path") from None
            add(relative)
    return artifacts


def _artifact_projection(artifacts: list[Artifact]) -> dict[str, Any]:
    digest = hashlib.sha256()
    for artifact in artifacts:
        path_bytes = artifact.path.encode("utf-8")
        digest.update(len(path_bytes).to_bytes(8, "big"))
        digest.update(path_bytes)
        digest.update(artifact.size.to_bytes(8, "big"))
        digest.update(bytes.fromhex(artifact.sha256))
    return {
        "bytes": sum(item.size for item in artifacts),
        "file_count": len(artifacts),
        "files": [
            {"path": item.path, "sha256": item.sha256, "size": item.size} for item in artifacts
        ],
        "sha256": digest.hexdigest(),
    }


def _ensure_django() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "website.settings.test")
    try:
        from django import setup
        from django.apps import apps

        if not apps.ready:
            setup()
    except Exception:
        raise ContentUpdateError("django_setup_failed") from None


def _family_contract(family: str) -> FamilyContract:
    if family not in FAMILIES:
        raise ContentUpdateError("family_invalid")
    _ensure_django()
    if family == "docs":
        from content.docs_projection import DOCS_SOURCE_REVISION

        revision = DOCS_SOURCE_REVISION
    elif family == "faq":
        from content.faq_data import FAQ_SOURCE_REVISION

        revision = FAQ_SOURCE_REVISION
    else:
        from scripts.projection_build.public_projection_source import EXPECTED_REVISIONS

        revision = EXPECTED_REVISIONS["courses" if family == "courses" else "wiki"]
    return FamilyContract(
        family=family,
        source_repository=_SOURCE_REPOSITORIES[family],
        source_revision=revision,
        projection_paths=_DECLARED_PROJECTION_PATHS[family],
        asset_roots=_ASSET_ROOTS[family],
    )


def _projection_for_family(family: str) -> dict[str, Any]:
    try:
        if family == "docs":
            from scripts.prod.import_docs import REVIEWED_PATH, load_reviewed_docs

            return load_reviewed_docs(REVIEWED_PATH)
        if family == "faq":
            from scripts.prod.import_faq import REVIEWED_PATH, load_reviewed_faq

            return load_reviewed_faq(REVIEWED_PATH)
        # The reviewed source files, not the database: this check asks whether an
        # upstream update has landed, which is a question about the files the
        # ingest reads.
        from scripts.projection_build.public_projection_source import load_checked_projection

        return load_checked_projection()
    except ContentUpdateError:
        raise
    except Exception:
        raise ContentUpdateError("projection_validation_failed") from None


def _source_from_projection(family: str, projection: Mapping[str, Any]) -> tuple[str, str]:
    if family in _PUBLIC_SOURCE_MANIFEST_KEYS:
        manifest = projection.get("manifest")
        if not isinstance(manifest, Mapping):
            raise ContentUpdateError("source_pin_missing")
        sources = manifest.get("sources")
        source_key = _PUBLIC_SOURCE_MANIFEST_KEYS[family]
        source = sources.get(source_key) if isinstance(sources, Mapping) else None
    else:
        source = projection.get("source")
    if not isinstance(source, Mapping):
        raise ContentUpdateError("source_pin_missing")
    repository = source.get("repository")
    if family == "docs":
        repository_matches = repository == "https://github.com/DataTalksClub/docs"
    else:
        repository_matches = repository == _SOURCE_REPOSITORIES[family]
    revision = source.get("revision")
    if not repository_matches or not isinstance(revision, str):
        raise ContentUpdateError("source_pin_mismatch")
    return _SOURCE_REPOSITORIES[family], revision


def _validate_public_artifact_bindings(
    family: str,
    projection: Mapping[str, Any],
    artifacts: list[Artifact],
) -> None:
    if family not in _PUBLIC_SOURCE_MANIFEST_KEYS:
        return
    manifest = projection.get("manifest")
    expected = manifest.get("artifacts") if isinstance(manifest, Mapping) else None
    by_path = {item.path: item.sha256 for item in artifacts}
    if not isinstance(expected, Mapping):
        raise ContentUpdateError("projection_artifact_manifest_missing")
    for relative in _DECLARED_PROJECTION_PATHS[family]:
        filename = PurePosixPath(relative).name
        if filename == "manifest.json":
            continue
        if expected.get(filename) != by_path.get(relative):
            raise ContentUpdateError("projection_artifact_checksum_mismatch")


def _validate_faq_asset_bindings(projection: Mapping[str, Any], artifacts: list[Artifact]) -> None:
    artifact_paths = {item.path for item in artifacts}
    courses = projection.get("courses")
    if not isinstance(courses, list):
        raise ContentUpdateError("faq_projection_shape_invalid")
    for course in courses:
        if not isinstance(course, Mapping):
            raise ContentUpdateError("faq_projection_shape_invalid")
        course_slug = course.get("slug")
        if not isinstance(course_slug, str) or not course_slug:
            raise ContentUpdateError("faq_projection_shape_invalid")
        sections = course.get("sections")
        if not isinstance(sections, list):
            raise ContentUpdateError("faq_projection_shape_invalid")
        for section in sections:
            if not isinstance(section, Mapping) or not isinstance(section.get("questions"), list):
                raise ContentUpdateError("faq_projection_shape_invalid")
            for question in section["questions"]:
                if not isinstance(question, Mapping):
                    raise ContentUpdateError("faq_projection_shape_invalid")
                images = question.get("images", [])
                if not isinstance(images, list):
                    raise ContentUpdateError("faq_projection_shape_invalid")
                for image in images:
                    if not isinstance(image, Mapping):
                        raise ContentUpdateError("faq_projection_shape_invalid")
                    source_path = image.get("source_path")
                    if not isinstance(source_path, str):
                        raise ContentUpdateError("faq_asset_source_missing")
                    try:
                        source = _relative_path(source_path)
                        if source.parts[:2] != ("images", course_slug) or len(source.parts) != 3:
                            raise ContentUpdateError("faq_asset_source_invalid")
                        relative = (
                            PurePosixPath("content/faq_assets")
                            / PurePosixPath(course_slug)
                            / PurePosixPath(source.name)
                        ).as_posix()
                    except ContentUpdateError:
                        raise ContentUpdateError("faq_asset_source_invalid") from None
                    if relative not in artifact_paths:
                        raise ContentUpdateError("faq_asset_missing")


def _family_counts(family: str, projection: Mapping[str, Any]) -> dict[str, int]:
    try:
        if family == "docs":
            return {
                "assets": len(projection["assets"]),
                "pages": len(projection["pages"]),
            }
        if family == "faq":
            counts = projection["counts"]
            return {key: int(counts[key]) for key in ("assets", "courses", "questions", "sections")}
        if family == "courses":
            return {"courses": len(projection["courses"])}
        graph = projection["wiki_graph"]
        search = projection["wiki_search"]
        return {
            "graph_links": len(graph["links"]),
            "graph_nodes": len(graph["nodes"]),
            "search_documents": len(search["docs"]),
            "wiki_pages": len(projection["wiki"]),
        }
    except (KeyError, TypeError):
        raise ContentUpdateError("projection_counts_invalid") from None


def _safe_report_value(value: object, *, key: str = "") -> None:
    if isinstance(value, str):
        value_terms: tuple[str, ...] = _REPORT_SENSITIVE_VALUE_TERMS
        if key == "path":
            value_terms = tuple(term for term in value_terms if term != "email")
        if any(term in value.casefold() for term in value_terms):
            # Public asset filenames are allowed to mention ordinary product concepts such as
            # "submission"; identities that could expose private course/account data are not.
            raise ContentUpdateError("report_redaction_failed")
        if any(character in value for character in ("\r", "\n")):
            raise ContentUpdateError("report_redaction_failed")
    elif isinstance(value, Mapping):
        for raw_key, item in value.items():
            if not isinstance(raw_key, str) or any(
                term in raw_key.casefold() for term in _REPORT_SENSITIVE_TERMS
            ):
                raise ContentUpdateError("report_redaction_failed")
            _safe_report_value(item, key=raw_key)
    elif isinstance(value, list):
        for item in value:
            _safe_report_value(item, key=key)


def _passing_report(
    contract: FamilyContract,
    projection: Mapping[str, Any],
    artifacts: list[Artifact],
    counts: dict[str, int],
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "checks": {name: "pass" for name in _CHECK_NAMES},
        "contract_version": CONTRACT_VERSION,
        "counts": counts,
        "errors": [],
        "family": contract.family,
        "projection": _artifact_projection(artifacts),
        "schema_version": SCHEMA_VERSION,
        "source": {
            "repository": contract.source_repository,
            "revision": contract.source_revision,
        },
        "status": "pass",
    }
    report["report_sha256"] = _sha256(_canonical_json(report))
    return report


def _failed_report(contract: FamilyContract, code: str) -> dict[str, Any]:
    report: dict[str, Any] = {
        "checks": {name: "not_run" for name in _CHECK_NAMES},
        "contract_version": CONTRACT_VERSION,
        "counts": {},
        "errors": [code if CODE_RE.fullmatch(code) else "content_update_failed"],
        "family": contract.family,
        "projection": {"bytes": 0, "file_count": 0, "files": [], "sha256": _sha256(b"")},
        "schema_version": SCHEMA_VERSION,
        "source": {
            "repository": contract.source_repository,
            "revision": contract.source_revision,
        },
        "status": "fail",
    }
    report["report_sha256"] = _sha256(_canonical_json(report))
    return report


def validate_report(payload: object) -> dict[str, Any]:
    """Validate a report without reading projected content or emitting its values."""

    if not isinstance(payload, dict):
        raise ContentUpdateError("report_shape_invalid")
    expected_keys = {
        "checks",
        "contract_version",
        "counts",
        "errors",
        "family",
        "projection",
        "report_sha256",
        "schema_version",
        "source",
        "status",
    }
    if set(payload) != expected_keys:
        raise ContentUpdateError("report_shape_invalid")
    if (
        payload["schema_version"] != SCHEMA_VERSION
        or payload["contract_version"] != CONTRACT_VERSION
    ):
        raise ContentUpdateError("report_version_invalid")
    family = payload["family"]
    if family not in FAMILIES:
        raise ContentUpdateError("report_family_invalid")
    source = payload["source"]
    if not isinstance(source, dict) or set(source) != {"repository", "revision"}:
        raise ContentUpdateError("report_source_invalid")
    if (
        source["repository"] != _SOURCE_REPOSITORIES[family]
        or not isinstance(source["revision"], str)
        or not SOURCE_REVISION_RE.fullmatch(source["revision"])
    ):
        raise ContentUpdateError("report_source_invalid")
    checks = payload["checks"]
    if (
        not isinstance(checks, dict)
        or set(checks) != set(_CHECK_NAMES)
        or any(value not in _CHECK_STATES for value in checks.values())
    ):
        raise ContentUpdateError("report_checks_invalid")
    projection = payload["projection"]
    if not isinstance(projection, dict) or set(projection) != {
        "bytes",
        "file_count",
        "files",
        "sha256",
    }:
        raise ContentUpdateError("report_projection_invalid")
    if (
        not isinstance(projection["bytes"], int)
        or isinstance(projection["bytes"], bool)
        or projection["bytes"] < 0
        or projection["bytes"] > MAX_TOTAL_BYTES
        or not isinstance(projection["file_count"], int)
        or isinstance(projection["file_count"], bool)
        or projection["file_count"] < 0
        or projection["file_count"] > MAX_FILE_COUNT
        or not isinstance(projection["sha256"], str)
        or not SHA256_RE.fullmatch(projection["sha256"])
    ):
        raise ContentUpdateError("report_projection_invalid")
    files = projection["files"]
    if not isinstance(files, list) or len(files) != projection["file_count"]:
        raise ContentUpdateError("report_files_invalid")
    allowed_paths = set(_DECLARED_PROJECTION_PATHS[family])
    allowed_prefixes = tuple(f"{root}/" for root in _ASSET_ROOTS[family])
    seen: set[str] = set()
    total_bytes = 0
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "size"}:
            raise ContentUpdateError("report_files_invalid")
        path = item["path"]
        if (
            not isinstance(path, str)
            or path in seen
            or (path not in allowed_paths and not path.startswith(allowed_prefixes))
        ):
            raise ContentUpdateError("report_files_invalid")
        _relative_path(path)
        if (
            not isinstance(item["size"], int)
            or isinstance(item["size"], bool)
            or item["size"] < 0
            or item["size"] > MAX_FILE_BYTES
        ):
            raise ContentUpdateError("report_files_invalid")
        if not isinstance(item["sha256"], str) or not SHA256_RE.fullmatch(item["sha256"]):
            raise ContentUpdateError("report_files_invalid")
        seen.add(path)
        total_bytes += item["size"]
    if total_bytes != projection["bytes"]:
        raise ContentUpdateError("report_files_invalid")
    if payload["status"] == "pass":
        if not set(_DECLARED_PROJECTION_PATHS[family]).issubset(seen):
            raise ContentUpdateError("report_files_invalid")
        if _ASSET_ROOTS[family] and not any(
            path.startswith(tuple(f"{root}/" for root in _ASSET_ROOTS[family])) for path in seen
        ):
            raise ContentUpdateError("report_files_invalid")
    digest = hashlib.sha256()
    for item in files:
        path_bytes = item["path"].encode("utf-8")
        digest.update(len(path_bytes).to_bytes(8, "big"))
        digest.update(path_bytes)
        digest.update(item["size"].to_bytes(8, "big"))
        digest.update(bytes.fromhex(item["sha256"]))
    if digest.hexdigest() != projection["sha256"]:
        raise ContentUpdateError("report_projection_digest_invalid")
    counts = payload["counts"]
    if not isinstance(counts, dict) or any(
        not isinstance(key, str)
        or not re.fullmatch(r"^[a-z][a-z0-9_]{0,47}$", key)
        or not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
        for key, value in counts.items()
    ):
        raise ContentUpdateError("report_counts_invalid")
    errors = payload["errors"]
    if (
        not isinstance(errors, list)
        or len(errors) > 4
        or any(not isinstance(error, str) or not CODE_RE.fullmatch(error) for error in errors)
    ):
        raise ContentUpdateError("report_errors_invalid")
    status = payload["status"]
    if status not in {"pass", "fail"}:
        raise ContentUpdateError("report_status_invalid")
    if status == "pass" and (errors or any(value != "pass" for value in checks.values())):
        raise ContentUpdateError("report_success_invalid")
    if status == "fail" and not errors:
        raise ContentUpdateError("report_failure_invalid")
    identity = dict(payload)
    digest = identity.pop("report_sha256")
    if (
        not isinstance(digest, str)
        or not SHA256_RE.fullmatch(digest)
        or digest != _sha256(_canonical_json(identity))
    ):
        raise ContentUpdateError("report_digest_invalid")
    _safe_report_value(payload)
    return payload


def build_report(*, repository: str | Path, family: str) -> dict[str, Any]:
    """Build and validate one family report from the current repository tree."""

    contract = _family_contract(family)
    root = Path(repository).resolve()
    if not root.is_dir():
        raise ContentUpdateError("repository_missing")
    if root != Path(__file__).resolve().parents[1]:
        raise ContentUpdateError("repository_mismatch")
    artifacts = _collect_artifacts(root, contract)
    projection = _projection_for_family(family)
    source_repository, source_revision = _source_from_projection(family, projection)
    if (
        source_repository != contract.source_repository
        or source_revision != contract.source_revision
    ):
        raise ContentUpdateError("source_pin_mismatch")
    _validate_public_artifact_bindings(family, projection, artifacts)
    if family == "faq":
        _validate_faq_asset_bindings(projection, artifacts)
    counts = _family_counts(family, projection)
    report = _passing_report(contract, projection, artifacts, counts)
    return validate_report(report)


def report_summary(payload: Mapping[str, Any]) -> str:
    report = validate_report(dict(payload))
    counts = (
        ", ".join(f"{key}={value}" for key, value in sorted(report["counts"].items())) or "none"
    )
    projection = report["projection"]
    lines = [
        f"## Content update: {report['family']}",
        "",
        f"- Status: `{report['status']}`",
        f"- Source: `{report['source']['repository']}@{report['source']['revision']}`",
        f"- Projection: `{projection['file_count']}` files, `{projection['bytes']}` bytes",
        f"- Projection SHA-256: `{projection['sha256']}`",
        f"- Counts: `{counts}`",
    ]
    if report["errors"]:
        lines.append(f"- Diagnostics: `{', '.join(report['errors'])}`")
    lines.append("")
    return "\n".join(lines)


def _write_report(destination: Path, payload: Mapping[str, Any]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent,
            prefix=".content-update-",
            suffix=".tmp",
            mode="w",
            encoding="utf-8",
            delete=False,
        ) as handle:
            temporary = handle.name
            handle.write(serialized)
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    finally:
        if temporary is not None and Path(temporary).exists():
            Path(temporary).unlink()


def _load_report(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ContentUpdateError("report_unreadable") from None
    return validate_report(payload)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", choices=FAMILIES)
    parser.add_argument("--repository", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--report", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.summary:
        if args.report is None or args.output is not None or args.family is not None:
            print("content_update_failed:summary_arguments_invalid", file=sys.stderr)
            return 2
        try:
            print(report_summary(_load_report(args.report)), end="")
        except ContentUpdateError as exc:
            print(f"content_update_failed:{exc.code}", file=sys.stderr)
            return 1
        return 0
    if args.family is None or args.output is None or args.report is not None:
        print("content_update_failed:arguments_invalid", file=sys.stderr)
        return 2
    try:
        report = build_report(repository=args.repository, family=args.family)
        _write_report(args.output, report)
    except ContentUpdateError as exc:
        error_code = exc.code
    except Exception:
        # CI output must remain content-free even if an unexpected filesystem or dependency error
        # escapes one of the bounded validators.
        error_code = "content_update_failed"
    else:
        return 0
    try:
        contract = _family_contract(args.family)
        _write_report(args.output, _failed_report(contract, error_code))
    except Exception:
        # The normal contract is available before validation.  This fallback remains one safe
        # line and never copies an exception or a source value into CI output.
        pass
    print(f"content_update_failed:{args.family}:{error_code}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
