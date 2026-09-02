#!/usr/bin/env python3
"""Generate the reviewed course-module preparation manifest from local checkouts.

``courses.services.local_course_modules`` consumes a pinned snapshot manifest: for each
of the three reviewed 2026 module cohorts it needs the repository identity, the exact
commit, an absolute checkout ``root``, a SHA-256 per selected file, and the deterministic
``snapshot_sha256`` over that selection.  Until now that manifest was produced by hand,
so ``make production-prep-local`` could not be replayed from a clean machine.

This builder closes that gap.  It selects exactly the files the course-repository parser
reads -- the manifest YAMLs at their only valid placements, the Markdown they reference,
and the lesson code attachments named in lesson frontmatter -- and nothing else.  It
never widens the selection to "everything in the repository", so unrelated repository
content stays outside the snapshot boundary.

The builder is read-only with respect to the checkouts and writes one JSON file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any

import django
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# The reviewed cohort/repository/digest contract lives with its consumer, so import it
# rather than restating it here.  That module reaches Django models, so the app registry
# has to be ready first; no database connection is opened.
os.environ.setdefault("DTC_ENVIRONMENT", "local")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "website.settings.local")
django.setup()

from courses.services.local_course_modules import (  # noqa: E402
    PREPARATION_SCHEMA_VERSION,
    TARGET_COHORTS,
    TARGET_REPOSITORIES,
    commit_is_public,
    snapshot_checksum,
)

# The reviewed source UUIDs are identity, not derived data: they must stay stable across
# regenerations so the curriculum importer keeps recognising an already-imported course.
SOURCE_UUIDS = {
    "llm-zoomcamp": "7736c1e6-5d66-4286-8180-b1eef3f83a84",
    "ml-zoomcamp": "10000000-0000-4000-8000-000000000104",
    "ai-dev-tools-zoomcamp": "10000000-0000-4000-8000-000000000103",
}
DEFAULT_CHECKOUT_DIRNAME = {
    "llm-zoomcamp": "llm-zoomcamp",
    "ml-zoomcamp": "ml-zoomcamp",
    "ai-dev-tools-zoomcamp": "ai-dev-tools-zoomcamp",
}


class ManifestBuildError(RuntimeError):
    """A bounded refusal to describe a checkout."""


def _git(root: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ManifestBuildError("git_unavailable") from error
    if completed.returncode != 0:
        raise ManifestBuildError(f"git_failed: {' '.join(arguments)}")
    return completed.stdout


def _tracked_files(root: Path) -> frozenset[str]:
    return frozenset(line for line in _git(root, "ls-files", "-z").split("\0") if line)


def _load_yaml(root: Path, relative: str) -> dict[str, Any]:
    try:
        loaded = yaml.safe_load((root / relative).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise ManifestBuildError(f"unreadable_manifest: {relative}") from error
    if not isinstance(loaded, dict):
        raise ManifestBuildError(f"invalid_manifest: {relative}")
    return loaded


def _manifest_paths(tracked: Iterable[str]) -> list[str]:
    """Return the YAML manifests at the placements the parser accepts."""

    selected: list[str] = []
    for path in tracked:
        parts = PurePosixPath(path).parts
        name = parts[-1]
        if name == "course.yaml" and parts == ("course.yaml",):
            selected.append(path)
        elif name == "module.yaml" and (
            (len(parts) == 2 and parts[0] != "cohorts")
            or (len(parts) == 4 and parts[0] == "cohorts")
        ):
            selected.append(path)
        elif name == "cohort.yaml" and len(parts) == 3 and parts[0] == "cohorts":
            selected.append(path)
        elif name == "homework.yaml" and len(parts) == 4 and parts[0] == "cohorts":
            selected.append(path)
    return sorted(selected)


def _resolve_beside(manifest_path: str, relative: str) -> str:
    return posixpath.normpath((PurePosixPath(manifest_path).parent / relative).as_posix())


def _frontmatter_code_paths(root: Path, markdown_path: str) -> list[str]:
    """Return the lesson code attachments a lesson's frontmatter references."""

    try:
        text = (root / markdown_path).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return []
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return []
    closing = next((index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"), 0)
    if not closing:
        return []
    try:
        mapping = yaml.safe_load("".join(lines[1:closing]))
    except yaml.YAMLError:
        return []
    if not isinstance(mapping, dict):
        return []
    entries = mapping.get("code") or []
    if not isinstance(entries, list):
        return []
    resolved: list[str] = []
    for entry in entries:
        if isinstance(entry, dict) and isinstance(entry.get("path"), str):
            resolved.append(_resolve_beside(markdown_path, entry["path"]))
    return resolved


def _referenced_paths(root: Path, manifest_path: str) -> list[str]:
    """Return every non-manifest file one YAML manifest pulls into the snapshot."""

    mapping = _load_yaml(root, manifest_path)
    name = PurePosixPath(manifest_path).name
    references: list[str] = []
    if name == "course.yaml":
        description_path = mapping.get("description_path")
        if isinstance(description_path, str):
            references.append(posixpath.normpath(description_path))
    elif name == "homework.yaml":
        instructions_path = mapping.get("instructions_path")
        if isinstance(instructions_path, str):
            references.append(_resolve_beside(manifest_path, instructions_path))
    elif name == "module.yaml":
        for unit in mapping.get("units") or []:
            if not isinstance(unit, dict) or not isinstance(unit.get("path"), str):
                continue
            unit_path = _resolve_beside(manifest_path, unit["path"])
            references.append(unit_path)
            references.extend(_frontmatter_code_paths(root, unit_path))
    return references


def _selected_files(root: Path) -> list[str]:
    tracked = _tracked_files(root)
    selected: set[str] = set()
    for manifest_path in _manifest_paths(tracked):
        selected.add(manifest_path)
        for reference in _referenced_paths(root, manifest_path):
            if reference not in tracked:
                raise ManifestBuildError(f"referenced_file_untracked: {reference}")
            selected.add(reference)
    if "course.yaml" not in selected:
        raise ManifestBuildError("course_manifest_missing")
    return sorted(selected)


def _file_checksums(root: Path, relative_paths: Iterable[str]) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for relative in relative_paths:
        candidate = root / relative
        if candidate.is_symlink() or not candidate.is_file():
            raise ManifestBuildError(f"unusable_source_file: {relative}")
        checksums[relative] = hashlib.sha256(candidate.read_bytes()).hexdigest()
    return checksums


def _source_record(
    stable_id: str,
    root: Path,
    *,
    unpublished_commit_reason: str = "",
) -> dict[str, Any]:
    root = root.resolve(strict=False)
    if not root.is_dir():
        raise ManifestBuildError(f"checkout_missing: {stable_id} at {root}")
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    if branch != "main":
        raise ManifestBuildError(f"checkout_not_on_main: {stable_id} is on {branch!r}")
    if _git(root, "status", "--porcelain=v1", "--untracked-files=all").strip():
        raise ManifestBuildError(f"checkout_dirty: {stable_id}")
    owner, name = TARGET_REPOSITORIES[stable_id]
    commit_sha = _git(root, "rev-parse", "HEAD").strip()
    # A commit no public reader can resolve makes every source-derived link on
    # the published page -- the edit link, the raw image URL, a source path a
    # reader follows -- a 404.  Refuse to describe such a checkout unless the
    # operator states why the import proceeds anyway.
    commit_public = commit_is_public(root, owner=owner, name=name, commit_sha=commit_sha)
    if not commit_public and not unpublished_commit_reason:
        raise ManifestBuildError(
            f"commit_not_public: {stable_id} at {commit_sha} is not on a branch of "
            f"https://github.com/{owner}/{name}; push it, or pass "
            f"--allow-unpublished-commit with the reason it may ship unpublished"
        )
    checksums = _file_checksums(root, _selected_files(root))
    record = {
        "source_uuid": SOURCE_UUIDS[stable_id],
        "source_stable_id": stable_id,
        "repository_owner": owner,
        "repository_name": name,
        "repository_branch": branch,
        "commit_sha": commit_sha,
        "cohort_identifier": TARGET_COHORTS[stable_id],
        "root": str(root),
        "files": checksums,
        "snapshot_sha256": snapshot_checksum(checksums),
    }
    if not commit_public:
        record["unpublished_commit_reason"] = unpublished_commit_reason
    return record


def build_manifest(
    checkouts: dict[str, Path],
    *,
    unpublished_commit_reason: str = "",
) -> dict[str, Any]:
    return {
        "schema_version": PREPARATION_SCHEMA_VERSION,
        "sources": [
            _source_record(
                stable_id,
                checkouts[stable_id],
                unpublished_commit_reason=unpublished_commit_reason,
            )
            for stable_id in TARGET_COHORTS
        ],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--checkout-root",
        type=Path,
        required=True,
        help=(
            "Directory holding the three course checkouts, named llm-zoomcamp, "
            "ml-zoomcamp, and ai-dev-tools-zoomcamp unless overridden."
        ),
    )
    for stable_id in TARGET_COHORTS:
        parser.add_argument(
            f"--{stable_id}-checkout",
            type=Path,
            default=None,
            help=f"Explicit checkout for {stable_id}.",
        )
    parser.add_argument("--output", type=Path, required=True, help="Manifest JSON to write.")
    parser.add_argument(
        "--allow-unpublished-commit",
        default="",
        metavar="REASON",
        help=(
            "Describe a checkout whose commit is not reachable on the public GitHub "
            "repository, recording this reason with it.  Without this the builder "
            "refuses, because the imported pages would carry source links nobody "
            "outside the operator's machine can resolve."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    checkouts = {
        stable_id: (
            getattr(args, f"{stable_id.replace('-', '_')}_checkout")
            or args.checkout_root / DEFAULT_CHECKOUT_DIRNAME[stable_id]
        )
        for stable_id in TARGET_COHORTS
    }
    try:
        manifest = build_manifest(
            checkouts,
            unpublished_commit_reason=args.allow_unpublished_commit.strip(),
        )
    except ManifestBuildError as error:
        print(f"course module manifest refused: {error}", file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "sources": [
                    {
                        "source_stable_id": source["source_stable_id"],
                        "commit_sha": source["commit_sha"],
                        "files": len(source["files"]),
                        "snapshot_sha256": source["snapshot_sha256"],
                        "commit_public": "unpublished_commit_reason" not in source,
                        "unpublished_commit_reason": source.get(
                            "unpublished_commit_reason", ""
                        ),
                    }
                    for source in manifest["sources"]
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
