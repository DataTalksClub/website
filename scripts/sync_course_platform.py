#!/usr/bin/env python3
"""Synchronize a reviewed Course Management Platform commit into this site.

The adopted course platform is a literal copy with a small, explicit target-owned
overlay.  This command deliberately treats the source repository as an upstream
artifact, rather than as a second Django project to merge.  It can prepare a
deterministic report without changing the target (the default), or apply a clean
non-conflicting commit with ``--apply``.

All source material is read from a clean, detached commit.  The command refuses
deletions, renames, migration rewrites, target-owned collisions, and changes to
files listed in ``integration-patched-files.tsv``.  Such changes need a reviewed
integration update before the source pin can move.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ``uv run python scripts/sync_course_platform.py`` is the documented local
# command. Add the repository root when Python executes this file directly so
# the shared adoption verifier remains importable as a package module.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.verify_course_platform_adoption import (
    ALLOWLIST,
    MANIFEST,
    PATCH_MANIFEST,
    TARGET_INTEGRATION_MANIFEST,
    Entry,
    IntegrationPatchEntry,
    destination_for,
    digest,
    read_manifest,
    read_patch_manifest,
    read_target_integration_manifest,
    render_manifest,
)
from scripts.verify_course_platform_adoption import (
    SOURCE_CHECKOUT as LEGACY_SOURCE_CHECKOUT,
)
from scripts.verify_course_platform_adoption import (
    SOURCE_COMMIT as LEGACY_SOURCE_COMMIT,
)

SOURCE_PIN = Path("_docs/adoption/course-platform/source-pin.json")
README = Path("_docs/adoption/course-platform/README.md")
DEFAULT_SOURCE_REPOSITORY = "https://github.com/DataTalksClub/course-management-platform.git"
SOURCE_PIN_SCHEMA = 1
REPORT_SCHEMA = 1
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
EXPECTED_ALLOWLIST = {
    "accounts": "accounts",
    "api": "api",
    "cadmin": "studio_courses",
    "course_management": "course_management",
    "courses": "courses",
    "data": "data",
    "e2e": "e2e",
    "scripts": "scripts",
    "templates": "course_platform_templates",
}


class SyncFailure(Exception):
    """A preflight failure that must leave the target untouched."""


@dataclass(frozen=True)
class SourcePin:
    repository: str
    commit: str
    checkout: str


@dataclass(frozen=True)
class ChangedPath:
    status: str
    old_path: str | None
    new_path: str | None
    classification: str
    destination: str | None = None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": self.status,
            "classification": self.classification,
        }
        if self.old_path is not None:
            result["old_path"] = self.old_path
        if self.new_path is not None:
            result["new_path"] = self.new_path
        if self.destination is not None:
            result["destination"] = self.destination
        return result


@dataclass(frozen=True)
class OverlayConflict:
    source_path: str
    destination: str
    old_source_sha256: str
    new_source_sha256: str
    target_overlay_sha256: str
    recorded_overlay_sha256: str
    rationale: str

    def as_dict(self) -> dict[str, str]:
        return {
            "source_path": self.source_path,
            "destination": self.destination,
            "old_source_sha256": self.old_source_sha256,
            "new_source_sha256": self.new_source_sha256,
            "target_overlay_sha256": self.target_overlay_sha256,
            "recorded_overlay_sha256": self.recorded_overlay_sha256,
            "rationale": self.rationale,
        }


@dataclass
class SyncPlan:
    repository: str
    current_commit: str
    target_commit: str
    source_checkout: str
    changed_paths: list[ChangedPath] = field(default_factory=list)
    copy_entries: list[Entry] = field(default_factory=list)
    conflicts: list[OverlayConflict] = field(default_factory=list)
    fatal_errors: list[str] = field(default_factory=list)
    target_entries: list[Entry] = field(default_factory=list)
    patch_entries: dict[str, IntegrationPatchEntry] = field(default_factory=dict)
    excluded_paths: list[ChangedPath] = field(default_factory=list)
    required_follow_up: list[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.changed_paths)

    @property
    def can_apply(self) -> bool:
        return not self.fatal_errors and not self.conflicts

    def report(self, *, action: str, applied: bool = False) -> dict[str, Any]:
        included = [
            path.as_dict() for path in self.changed_paths if path.classification == "allowlisted"
        ]
        excluded = [path.as_dict() for path in self.excluded_paths]
        status = "applied" if applied else "ready" if self.can_apply else "blocked"
        if not self.has_changes:
            status = "no_change" if not applied else "applied"
        return {
            "schema_version": REPORT_SCHEMA,
            "action": action,
            "status": status,
            "applied": applied,
            "source_repository": self.repository,
            "current_pinned_commit": self.current_commit,
            "requested_commit": self.target_commit,
            "source_checkout": self.source_checkout,
            "changed_paths": [path.as_dict() for path in self.changed_paths],
            "allowlisted_changes": included,
            "excluded_changes": excluded,
            "copy_entries": [
                {"source_path": entry.source, "destination_path": entry.destination}
                for entry in self.copy_entries
            ],
            "overlay_conflicts": [conflict.as_dict() for conflict in self.conflicts],
            "fatal_errors": sorted(self.fatal_errors),
            "required_follow_up": sorted(set(self.required_follow_up)),
        }


def _git(source: Path, *args: str, text: bool = True) -> str | bytes:
    result = subprocess.run(
        ["git", "-C", str(source), *args],
        check=True,
        capture_output=True,
        text=text,
    )
    return result.stdout


def _git_text(source: Path, *args: str) -> str:
    result = _git(source, *args, text=True)
    assert isinstance(result, str)
    return result


def _git_bytes(source: Path, *args: str) -> bytes:
    result = _git(source, *args, text=False)
    assert isinstance(result, bytes)
    return result


def _git_checked(source: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(source), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _require_sha(value: str, label: str) -> str:
    value = value.strip()
    if not SHA_RE.fullmatch(value):
        raise SyncFailure(f"{label} must be a 40-character lowercase commit SHA: {value!r}")
    return value


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SyncFailure(f"invalid source pin metadata: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SyncFailure(f"source pin metadata must be a JSON object: {path}")
    return value


def read_source_pin(repo: Path) -> SourcePin:
    """Read the current immutable source pin, with a baseline fallback."""

    path = repo / SOURCE_PIN
    if not path.exists():
        return SourcePin(
            repository=DEFAULT_SOURCE_REPOSITORY,
            commit=LEGACY_SOURCE_COMMIT,
            checkout=str(LEGACY_SOURCE_CHECKOUT),
        )
    value = _read_json(path)
    if value.get("schema_version") != SOURCE_PIN_SCHEMA:
        raise SyncFailure(f"unsupported source pin schema in {path}")
    repository = value.get("source_repository")
    if not isinstance(repository, str) or not repository.strip():
        raise SyncFailure(f"source pin repository is missing in {path}")
    raw_commit = value.get("source_commit")
    if not isinstance(raw_commit, str):
        raise SyncFailure(f"source pin commit is missing in {path}")
    commit = _require_sha(raw_commit, "source pin commit")
    checkout = value.get("source_checkout", str(LEGACY_SOURCE_CHECKOUT))
    if not isinstance(checkout, str) or not checkout.strip():
        raise SyncFailure(f"source pin checkout is missing in {path}")
    return SourcePin(repository=repository, commit=commit, checkout=checkout)


def render_source_pin(pin: SourcePin) -> str:
    return (
        json.dumps(
            {
                "schema_version": SOURCE_PIN_SCHEMA,
                "source_repository": pin.repository,
                "source_commit": pin.commit,
                "source_checkout": pin.checkout,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def _ensure_allowlist() -> None:
    if ALLOWLIST != EXPECTED_ALLOWLIST:
        raise SyncFailure("course-platform allowlist changed without a reviewed sync mapping")
    if len(set(ALLOWLIST.values())) != len(ALLOWLIST):
        raise SyncFailure("course-platform allowlist contains duplicate destination roots")


def _source_root(path: str | None) -> str | None:
    if not path:
        return None
    return path.split("/", 1)[0]


def _is_allowlisted_path(path: str | None, entries: dict[str, Entry]) -> bool:
    if path is None:
        return False
    if path in entries:
        return True
    root = _source_root(path)
    return root in ALLOWLIST


def _is_migration(path: str | None) -> bool:
    if not path:
        return False
    return "migrations" in path.split("/")


def _entry_from_blob(source: Path, commit: str, path: str) -> Entry:
    try:
        data = _git_bytes(source, "cat-file", "blob", f"{commit}:{path}")
    except subprocess.CalledProcessError as exc:
        raise SyncFailure(f"source path is not a regular blob: {commit}:{path}") from exc
    return Entry(path, destination_for(path), len(data), hashlib.sha256(data).hexdigest())


def entries_at_commit(source: Path, commit: str) -> list[Entry]:
    paths = _git_text(source, "ls-tree", "-r", "--name-only", commit, "--", *ALLOWLIST)
    entries: list[Entry] = []
    for path in sorted(path for path in paths.splitlines() if path):
        root = _source_root(path)
        if root not in ALLOWLIST:
            raise SyncFailure(f"unknown allowlisted source root: {root or path}")
        entries.append(_entry_from_blob(source, commit, path))
    if len({entry.destination for entry in entries}) != len(entries):
        raise SyncFailure("source allowlist maps multiple paths to one destination")
    return entries


def _validate_manifest(entries: list[Entry]) -> None:
    destinations: set[str] = set()
    for entry in entries:
        if _source_root(entry.source) not in ALLOWLIST:
            raise SyncFailure(f"manifest contains an unknown allowlisted root: {entry.source}")
        try:
            expected_destination = destination_for(entry.source)
        except KeyError as exc:
            raise SyncFailure(f"manifest contains an unknown source root: {entry.source}") from exc
        if entry.destination != expected_destination:
            raise SyncFailure(
                f"manifest mapping differs from adoption allowlist: {entry.source} -> "
                f"{entry.destination} (expected {expected_destination})"
            )
        if entry.destination in destinations:
            raise SyncFailure(f"manifest contains a duplicate destination: {entry.destination}")
        destinations.add(entry.destination)


def _read_current_state(
    repo: Path, source: Path, current_commit: str
) -> tuple[list[Entry], dict[str, IntegrationPatchEntry], set[str]]:
    try:
        current_manifest = read_manifest(repo / MANIFEST)
        patch_entries = read_patch_manifest(repo / PATCH_MANIFEST)
    except (OSError, ValueError, SystemExit) as exc:
        raise SyncFailure(f"cannot read adoption manifests: {exc}") from exc
    _validate_manifest(current_manifest)
    expected_current = entries_at_commit(source, current_commit)
    expected_by_source = {entry.source: entry for entry in expected_current}
    recorded_by_source = {entry.source: entry for entry in current_manifest}
    if recorded_by_source != expected_by_source:
        raise SyncFailure("copied-files.tsv does not match the currently pinned CMP commit")
    unknown_patches = sorted(set(patch_entries) - {entry.destination for entry in current_manifest})
    if unknown_patches:
        raise SyncFailure(
            "integration patch is not an allowlisted copy: " + ", ".join(unknown_patches)
        )
    for entry in current_manifest:
        destination = repo / entry.destination
        if not destination.is_file() or destination.is_symlink():
            raise SyncFailure(f"missing or unsafe copied destination: {entry.destination}")
        actual_sha = digest(destination)
        actual_size = destination.stat().st_size
        patch = patch_entries.get(entry.destination)
        expected_sha = patch.sha256 if patch else entry.sha256
        expected_size = patch.size if patch else entry.size
        if (actual_size, actual_sha) != (expected_size, expected_sha):
            raise SyncFailure(
                "target copy differs from recorded integration state: "
                f"{entry.destination} ({actual_size}, {actual_sha})"
            )
    target_owned: set[str] = set()
    shim_path = repo / TARGET_INTEGRATION_MANIFEST
    if shim_path.exists():
        try:
            target_owned = {
                entry.destination for entry in read_target_integration_manifest(shim_path)
            }
        except (OSError, ValueError, SystemExit) as exc:
            raise SyncFailure(f"cannot read target-owned compatibility manifest: {exc}") from exc
    return current_manifest, patch_entries, target_owned


def _parse_diff(
    source: Path, current: str, target: str
) -> list[tuple[str, str | None, str | None]]:
    raw = _git_bytes(
        source,
        "diff",
        "--name-status",
        "--find-renames",
        "--find-copies",
        "-z",
        current,
        target,
    )
    tokens = raw.split(b"\0")
    records: list[tuple[str, str | None, str | None]] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if not token:
            continue
        status = token.decode("utf-8")
        if status.startswith(("R", "C")):
            if index + 1 >= len(tokens):
                raise SyncFailure("malformed source rename/copy diff")
            old_path = tokens[index].decode("utf-8")
            new_path = tokens[index + 1].decode("utf-8")
            index += 2
            records.append((status, old_path, new_path))
        else:
            if index >= len(tokens):
                raise SyncFailure("malformed source diff")
            path = tokens[index].decode("utf-8")
            index += 1
            if status == "A":
                records.append((status, None, path))
            elif status == "D":
                records.append((status, path, None))
            else:
                records.append((status, path, path))
    return records


def _overlay_conflict(
    *,
    repo: Path,
    old_entry: Entry,
    new_entry: Entry,
    patch: IntegrationPatchEntry,
) -> OverlayConflict:
    destination = repo / new_entry.destination
    if not destination.is_file() or destination.is_symlink():
        raise SyncFailure(f"overlay destination is missing or unsafe: {new_entry.destination}")
    actual_sha = digest(destination)
    actual_size = destination.stat().st_size
    if (actual_size, actual_sha) != (patch.size, patch.sha256):
        raise SyncFailure(
            "integration overlay differs from its recorded evidence: "
            f"{new_entry.destination} ({actual_size}, {actual_sha})"
        )
    return OverlayConflict(
        source_path=new_entry.source,
        destination=new_entry.destination,
        old_source_sha256=old_entry.sha256,
        new_source_sha256=new_entry.sha256,
        target_overlay_sha256=actual_sha,
        recorded_overlay_sha256=patch.sha256,
        rationale=patch.rationale,
    )


def build_plan(
    *,
    repo: Path,
    source: Path,
    current_pin: SourcePin,
    target_commit: str,
    source_checkout: str | None = None,
) -> SyncPlan:
    """Build a complete preflight plan without changing target files."""

    _ensure_allowlist()
    current_commit = _require_sha(current_pin.commit, "current pinned commit")
    target_commit = _require_sha(target_commit, "requested commit")
    source_checkout = source_checkout or current_pin.checkout
    current_manifest, patches, target_owned = _read_current_state(repo, source, current_commit)
    target_entries = entries_at_commit(source, target_commit)
    old_by_source = {entry.source: entry for entry in current_manifest}
    new_by_source = {entry.source: entry for entry in target_entries}
    patch_destinations = set(patches)

    plan = SyncPlan(
        repository=current_pin.repository,
        current_commit=current_commit,
        target_commit=target_commit,
        source_checkout=source_checkout,
        target_entries=target_entries,
        patch_entries=patches,
    )

    for status, old_path, new_path in _parse_diff(source, current_commit, target_commit):
        old_allowlisted = _is_allowlisted_path(old_path, old_by_source)
        new_allowlisted = _is_allowlisted_path(new_path, new_by_source)
        allowlisted = old_allowlisted or new_allowlisted
        destination: str | None = None
        candidate = new_path if new_allowlisted else old_path
        if candidate is not None and new_allowlisted:
            destination = new_by_source[candidate].destination
        changed = ChangedPath(
            status=status,
            old_path=old_path,
            new_path=new_path,
            classification="allowlisted" if allowlisted else "excluded",
            destination=destination,
        )
        plan.changed_paths.append(changed)
        if not allowlisted:
            plan.excluded_paths.append(changed)

        if not allowlisted:
            continue
        if status.startswith(("R", "C")):
            plan.fatal_errors.append(
                f"allowlisted source {status[0].lower()} is not supported: {old_path} -> {new_path}"
            )
            continue
        if status == "D":
            plan.fatal_errors.append(f"allowlisted source deletion is not supported: {old_path}")
            continue
        if new_path is None or new_path not in new_by_source:
            plan.fatal_errors.append(f"allowlisted source path is missing from target: {new_path}")
            continue
        new_entry = new_by_source[new_path]
        old_entry = old_by_source.get(new_path)
        if (
            _is_migration(new_path)
            and old_entry is not None
            and old_entry.sha256 != new_entry.sha256
        ):
            plan.fatal_errors.append(f"existing migration replacement is not supported: {new_path}")
            continue
        if old_entry is None:
            destination_path = repo / new_entry.destination
            if destination_path.exists() or destination_path.is_symlink():
                owner = (
                    "target-owned" if new_entry.destination in target_owned else "existing target"
                )
                plan.fatal_errors.append(
                    f"new source destination collides with {owner} file: {new_entry.destination}"
                )
                continue
            plan.copy_entries.append(new_entry)
            continue
        if old_entry.destination != new_entry.destination:
            plan.fatal_errors.append(f"source mapping changed for {new_path}")
            continue
        if old_entry.sha256 == new_entry.sha256:
            continue
        if new_entry.destination in patch_destinations:
            try:
                plan.conflicts.append(
                    _overlay_conflict(
                        repo=repo,
                        old_entry=old_entry,
                        new_entry=new_entry,
                        patch=patches[new_entry.destination],
                    )
                )
            except SyncFailure as exc:
                plan.fatal_errors.append(str(exc))
        else:
            plan.copy_entries.append(new_entry)

    # Compare manifests independently of Git's rename heuristics.  This catches
    # mode-only changes, unusual diff records, and a stale allowlist early.
    removed = sorted(set(old_by_source) - set(new_by_source))
    for source_path in removed:
        if source_path not in {
            path.old_path
            for path in plan.changed_paths
            if path.classification == "allowlisted" and path.old_path
        }:
            plan.fatal_errors.append(f"allowlisted source deletion is not supported: {source_path}")
    if removed:
        plan.required_follow_up.append("restore deleted or renamed allowlisted source files")

    # Every existing copied destination must remain present.  A destination
    # disappearing due to a source-side rename/deletion is never removed here.
    for old_entry in current_manifest:
        if old_entry.source not in new_by_source:
            continue
        if not (repo / old_entry.destination).is_file():
            plan.fatal_errors.append(f"copied destination is missing: {old_entry.destination}")

    if plan.conflicts:
        plan.required_follow_up.append(
            "review each integration overlay conflict and update its target-owned evidence"
        )
    if plan.fatal_errors:
        plan.required_follow_up.append(
            "resolve all fail-closed source safety errors before applying"
        )
    return plan


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.cmp-sync-tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def _read_source_file(source: Path, entry: Entry) -> bytes:
    path = source / entry.source
    if not path.is_file() or path.is_symlink():
        raise SyncFailure(f"source copy path is missing or unsafe: {entry.source}")
    content = path.read_bytes()
    if len(content) != entry.size or hashlib.sha256(content).hexdigest() != entry.sha256:
        raise SyncFailure(f"source copy changed while preparing: {entry.source}")
    return content


def _updated_readme(repo: Path, target_commit: str) -> str | None:
    path = repo / README
    if not path.exists():
        return None
    content = path.read_text(encoding="utf-8")
    replacement, count = re.subn(
        r"(?m)^(Pinned source commit:\s*`)[0-9a-f]{40}(`\s*)$",
        rf"\g<1>{target_commit}\g<2>",
        content,
    )
    if count == 0:
        raise SyncFailure(f"adoption README has no pinned source commit line: {README}")
    if count != 1:
        raise SyncFailure(f"adoption README has multiple pinned source commit lines: {README}")
    return replacement


def apply_plan(*, repo: Path, source: Path, plan: SyncPlan) -> None:
    """Apply a preflighted plan.  All safety checks must have passed first."""

    if not plan.can_apply:
        raise SyncFailure("cannot apply a plan with conflicts or fatal errors")

    # Validate all target-owned text before the first destination is replaced.
    readme = _updated_readme(repo, plan.target_commit)

    staging = repo / ".tmp" / "cmp-sync-staging"
    if staging.exists():
        if staging.is_file() or staging.is_symlink():
            raise SyncFailure(f"unsafe sync staging path: {staging}")
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=False)
    try:
        staged: list[tuple[Path, Path]] = []
        for entry in plan.copy_entries:
            destination = repo / entry.destination
            staged_path = staging / entry.destination
            staged_path.parent.mkdir(parents=True, exist_ok=True)
            staged_path.write_bytes(_read_source_file(source, entry))
            staged_content = staged_path.read_bytes()
            if digest(staged_path) != entry.sha256 or len(staged_content) != entry.size:
                raise SyncFailure(f"staged source checksum mismatch: {entry.source}")
            staged.append((staged_path, destination))

        for staged_path, destination in staged:
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged_path, destination)

        _write_atomic(repo / MANIFEST, render_manifest(plan.target_entries))
        new_pin = SourcePin(
            repository=plan.repository,
            commit=plan.target_commit,
            checkout=plan.source_checkout,
        )
        _write_atomic(repo / SOURCE_PIN, render_source_pin(new_pin))
        if readme is not None:
            _write_atomic(repo / README, readme)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _resolve_target_commit(source: Path, source_ref: str) -> str:
    if source_ref.startswith("refs/heads/"):
        branch = source_ref.removeprefix("refs/heads/")
        candidates = [f"origin/{branch}", source_ref]
    elif source_ref.startswith("origin/"):
        candidates = [source_ref]
    elif source_ref in {"main", "master"}:
        candidates = [f"origin/{source_ref}", source_ref]
    else:
        candidates = [source_ref]
    for candidate in candidates:
        result = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "--verify", f"{candidate}^{{commit}}"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return _require_sha(result.stdout, "requested commit")
    raise SyncFailure(f"source ref does not resolve to a commit: {source_ref}")


def _assert_clean(source: Path) -> None:
    status = _git_text(source, "status", "--porcelain=v1", "--untracked-files=all")
    if status:
        raise SyncFailure(f"source checkout is dirty: {source}")


def _local_repository_is_dirty(repository: str) -> bool:
    path = Path(repository)
    if not path.is_dir() or not (path / ".git").exists():
        return False
    result = subprocess.run(
        ["git", "-C", str(path), "status", "--porcelain=v1", "--untracked-files=all"],
        check=True,
        capture_output=True,
        text=True,
    )
    return bool(result.stdout)


def prepare_source(
    *,
    repo: Path,
    repository: str,
    source_ref: str,
    current_commit: str,
    checkout: Path | None = None,
) -> tuple[Path, str, str]:
    """Clone/fetch a source and return ``(checkout, exact_sha, relative_path)``."""

    if _local_repository_is_dirty(repository):
        raise SyncFailure(f"source repository is dirty: {repository}")
    checkout = checkout or repo / ".tmp" / "cmp-source-sync"
    checkout = checkout if checkout.is_absolute() else repo / checkout
    checkout.parent.mkdir(parents=True, exist_ok=True)
    created = False
    if not checkout.exists():
        command = ["git", "clone", "--no-tags", "--no-checkout"]
        if Path(repository).is_dir():
            command.append("--no-local")
        command.extend([repository, str(checkout)])
        subprocess.run(command, check=True, capture_output=True, text=True)
        created = True
    elif not (checkout / ".git").exists():
        raise SyncFailure(f"source checkout is not a Git repository: {checkout}")
    if not created:
        _assert_clean(checkout)

    def has_commit(ref: str) -> bool:
        result = subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", "--verify", f"{ref}^{{commit}}"],
            check=False,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    # Existing fixture checkouts may already contain the requested commits and
    # need no remote.  A clone, or a checkout missing a commit, fetches only
    # immutable refs needed by this invocation.
    branch_ref = source_ref in {"main", "master"} or source_ref.startswith("refs/heads/")
    if branch_ref or not has_commit(source_ref):
        try:
            _git_checked(checkout, "fetch", "--no-tags", "origin", source_ref)
        except subprocess.CalledProcessError as exc:
            raise SyncFailure(f"source ref cannot be fetched: {source_ref}") from exc
    if not has_commit(current_commit):
        try:
            _git_checked(checkout, "fetch", "--no-tags", "origin", current_commit)
        except subprocess.CalledProcessError as exc:
            raise SyncFailure(f"pinned source commit cannot be fetched: {current_commit}") from exc
    target_commit = _resolve_target_commit(checkout, source_ref)
    _git_checked(checkout, "checkout", "--detach", target_commit)
    _assert_clean(checkout)
    try:
        relative = checkout.relative_to(repo)
        relative_text = str(relative)
    except ValueError:
        relative_text = str(checkout)
    return checkout, target_commit, relative_text


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-ref",
        default=None,
        help="CMP branch, tag, or full commit SHA (defaults to CMP main)",
    )
    parser.add_argument(
        "--source-repository",
        default=None,
        help="CMP Git repository (defaults to source-pin.json)",
    )
    parser.add_argument(
        "--source-checkout",
        type=Path,
        default=None,
        help="clean checkout to use/fetch instead of the project-local clone",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path(".tmp/course-platform-sync-report.json"),
        help="deterministic JSON report path",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="report only (the default)")
    mode.add_argument("--apply", action="store_true", help="apply a conflict-free sync")
    return parser.parse_args(argv)


def run(argv: Sequence[str] | None = None, *, repository_root: Path | None = None) -> int:
    args = parse_args(argv)
    repo = (repository_root or Path(__file__).resolve().parents[1]).resolve()
    action = "apply" if args.apply else "dry-run"
    report_path = args.report if args.report.is_absolute() else repo / args.report
    current_pin: SourcePin | None = None
    source_repository: str | None = args.source_repository
    try:
        current_pin = read_source_pin(repo)
        source_repository = args.source_repository or current_pin.repository
        source_ref = args.source_ref or "main"
        source, target_commit, checkout_text = prepare_source(
            repo=repo,
            repository=source_repository,
            source_ref=source_ref,
            current_commit=current_pin.commit,
            checkout=args.source_checkout,
        )
        plan = build_plan(
            repo=repo,
            source=source,
            current_pin=current_pin,
            target_commit=target_commit,
            source_checkout=checkout_text,
        )
        report = plan.report(action=action)
        write_report(report_path, report)
        if args.apply and not plan.can_apply:
            return 2
        if args.apply:
            apply_plan(repo=repo, source=source, plan=plan)
            write_report(report_path, plan.report(action=action, applied=True))
        return 0
    except (SyncFailure, subprocess.CalledProcessError, OSError) as exc:
        report = {
            "schema_version": REPORT_SCHEMA,
            "action": action,
            "status": "blocked",
            "applied": False,
            "source_repository": source_repository,
            "current_pinned_commit": current_pin.commit if current_pin else None,
            "requested_commit": args.source_ref
            if args.source_ref and SHA_RE.fullmatch(args.source_ref)
            else None,
            "source_checkout": str(args.source_checkout) if args.source_checkout else None,
            "changed_paths": [],
            "allowlisted_changes": [],
            "excluded_changes": [],
            "copy_entries": [],
            "overlay_conflicts": [],
            "fatal_errors": [str(exc)],
            "required_follow_up": ["resolve the preflight failure before retrying"],
        }
        write_report(report_path, report)
        print(str(exc), file=sys.stderr)
        return 2


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
