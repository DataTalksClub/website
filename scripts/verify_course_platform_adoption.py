#!/usr/bin/env python3
"""Verify the pinned Course Management Platform copy and its manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

SOURCE_COMMIT = "98a235283904b4ef9ad29e196298540756cf1bcc"
SOURCE_CHECKOUT = Path(".tmp/cmp-source-98a2352")
SOURCE_PIN = Path("_docs/adoption/course-platform/source-pin.json")
MANIFEST = Path("_docs/adoption/course-platform/copied-files.tsv")
PATCH_MANIFEST = Path("_docs/adoption/course-platform/integration-patched-files.tsv")
TARGET_INTEGRATION_MANIFEST = Path(
    "_docs/adoption/course-platform/target-owned-compatibility-shims.tsv"
)
CADMIN_REFERENCE_MANIFEST = Path("_docs/adoption/course-platform/cadmin-reference-allowlist.tsv")
TARGET_COMPATIBILITY_CLASSIFICATION = "target-owned compatibility shim"
REQUIRED_TARGET_COMPATIBILITY_SHIMS = {
    "cadmin/__init__.py",
    "cadmin/legacy_urls.py",
    "website/admin_api_urls.py",
    "website/admin_api_views.py",
}
ALLOWLIST = {
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


@dataclass(frozen=True)
class Entry:
    source: str
    destination: str
    size: int
    sha256: str

    def line(self) -> str:
        return f"{self.source}\t{self.destination}\t{self.size}\t{self.sha256}"


@dataclass(frozen=True)
class TargetIntegrationEntry:
    destination: str
    classification: str
    size: int
    sha256: str
    rationale: str


@dataclass(frozen=True)
class IntegrationPatchEntry:
    destination: str
    size: int
    sha256: str
    rationale: str

    def line(self) -> str:
        return f"{self.destination}\t{self.size}\t{self.sha256}\t{self.rationale}"


# Copied paths the target has deliberately removed.  The manifest is derived from the
# pinned upstream checkout, so a retired file stays recorded there; this is where the
# target says the destination is gone on purpose rather than missing by accident.
#
# ``scripts/load_rds_export.py`` was the broad CMP-export loader.  Its ``main()`` was
# already disabled, but its copy plan skipped only ``sqlite_sequence`` and
# ``django_migrations``, so re-enabling it would have copied ``django_session``,
# ``socialaccount_socialaccount``, ``socialaccount_socialapp`` and ``accounts_token``
# straight out of a production export.  Leaving a loaded weapon in the drawer with the
# safety on is not a safety measure; the production importers in ``scripts/prod/``
# replace it and name their forbidden tables explicitly.
RETIRED_ADOPTION_DESTINATIONS = frozenset(
    {
        "courses/models/course.py",
        "courses/tests/test_load_rds_export_script.py",
        "scripts/load_rds_export.py",
    }
)


def retired_adoption_destinations(repo: Path, destinations: Iterable[str]) -> set[str]:
    """Return copied paths retired by the target's migration/model consolidation."""

    return {
        destination
        for destination in destinations
        if not (repo / destination).is_file()
        and (
            destination.startswith("courses/migrations/")
            or destination in RETIRED_ADOPTION_DESTINATIONS
        )
    }


def run_git(source: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(source), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as source_file:
        for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def destination_for(source_path: str) -> str:
    legacy_template_prefix = "cadmin/templates/cadmin/"
    if source_path.startswith(legacy_template_prefix):
        suffix = source_path.removeprefix(legacy_template_prefix)
        return f"studio_courses/templates/studio_courses/{suffix}"
    root, separator, suffix = source_path.partition("/")
    destination_root = ALLOWLIST[root]
    return destination_root if not separator else f"{destination_root}/{suffix}"


def expected_entries(source: Path) -> list[Entry]:
    tracked = run_git(source, "ls-files", "--", *ALLOWLIST).splitlines()
    entries = []
    for source_path in sorted(tracked):
        absolute_source = source / source_path
        entries.append(
            Entry(
                source=source_path,
                destination=destination_for(source_path),
                size=absolute_source.stat().st_size,
                sha256=digest(absolute_source),
            )
        )
    return entries


def read_manifest(path: Path) -> list[Entry]:
    lines = path.read_text(encoding="utf-8").splitlines()
    expected_header = "source_path\tdestination_path\tsize_bytes\tsha256"
    if not lines or lines[0] != expected_header:
        raise SystemExit(f"invalid manifest header: {path}")
    entries = []
    for line in lines[1:]:
        source, destination, raw_size, sha256 = line.split("\t")
        entries.append(Entry(source, destination, int(raw_size), sha256))
    return entries


def render_manifest(entries: list[Entry]) -> str:
    header = "source_path\tdestination_path\tsize_bytes\tsha256"
    return "\n".join([header, *(entry.line() for entry in entries)]) + "\n"


def read_patch_manifest(path: Path) -> dict[str, IntegrationPatchEntry]:
    lines = path.read_text(encoding="utf-8").splitlines()
    expected_header = "destination_path\tsize_bytes\tsha256\trationale"
    if not lines or lines[0] != expected_header:
        raise SystemExit(f"invalid integration patch manifest header: {path}")
    patches = {}
    for line in lines[1:]:
        destination, raw_size, sha256, _rationale = line.split("\t", 3)
        patches[destination] = IntegrationPatchEntry(
            destination=destination,
            size=int(raw_size),
            sha256=sha256,
            rationale=_rationale,
        )
    return patches


def render_patch_manifest(entries: list[IntegrationPatchEntry]) -> str:
    header = "destination_path\tsize_bytes\tsha256\trationale"
    return "\n".join([header, *(entry.line() for entry in entries)]) + "\n"


def current_patch_entries(
    repo: Path,
    copied_entries: list[Entry],
    recorded_patches: dict[str, IntegrationPatchEntry],
) -> list[IntegrationPatchEntry]:
    entries: list[IntegrationPatchEntry] = []
    default_rationale = (
        "Retain the reviewed target-owned course/cohort implementation and record its drift "
        "from the pinned CMP copy."
    )
    for copied in copied_entries:
        destination = repo / copied.destination
        if not destination.is_file():
            continue
        actual_size = destination.stat().st_size
        actual_sha256 = digest(destination)
        if actual_size == copied.size and actual_sha256 == copied.sha256:
            continue
        previous = recorded_patches.get(copied.destination)
        entries.append(
            IntegrationPatchEntry(
                destination=copied.destination,
                size=actual_size,
                sha256=actual_sha256,
                rationale=previous.rationale if previous else default_rationale,
            )
        )
    return entries


def read_target_integration_manifest(path: Path) -> list[TargetIntegrationEntry]:
    lines = path.read_text(encoding="utf-8").splitlines()
    expected_header = "destination_path\tclassification\tsize_bytes\tsha256\trationale"
    if not lines or lines[0] != expected_header:
        raise SystemExit(f"invalid target integration manifest header: {path}")
    entries = []
    for line in lines[1:]:
        destination, classification, raw_size, sha256, rationale = line.split("\t", 4)
        entries.append(
            TargetIntegrationEntry(
                destination=destination,
                classification=classification,
                size=int(raw_size),
                sha256=sha256,
                rationale=rationale,
            )
        )
    return entries


def read_source_pin(repo: Path) -> tuple[str, Path]:
    """Return the current source commit and checkout from adoption metadata."""

    path = repo / SOURCE_PIN
    if not path.exists():
        return SOURCE_COMMIT, SOURCE_CHECKOUT
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"invalid source pin metadata: {path}: {exc}") from exc
    if not isinstance(metadata, dict) or metadata.get("schema_version") != 1:
        raise SystemExit(f"unsupported source pin metadata: {path}")
    commit = metadata.get("source_commit")
    checkout = metadata.get("source_checkout", str(SOURCE_CHECKOUT))
    if (
        not isinstance(commit, str)
        or len(commit) != 40
        or any(character not in "0123456789abcdef" for character in commit)
    ):
        raise SystemExit(f"invalid source pin commit: {path}")
    if not isinstance(checkout, str) or not checkout.strip():
        raise SystemExit(f"invalid source checkout path: {path}")
    return commit, Path(checkout)


def verify_source(source: Path, expected_commit: str = SOURCE_COMMIT) -> None:
    if run_git(source, "rev-parse", "HEAD").strip() != expected_commit:
        raise SystemExit(f"source checkout is not pinned to {expected_commit}")
    if run_git(source, "status", "--porcelain=v1", "--untracked-files=all"):
        raise SystemExit("source checkout is not clean")


def verify_destinations(
    repo: Path, entries: list[Entry], patches: dict[str, IntegrationPatchEntry]
) -> None:
    errors: list[str] = []
    manifest_destinations = {entry.destination for entry in entries}
    unknown_patches = sorted(set(patches) - manifest_destinations)
    errors.extend(
        f"integration patch is not an allowlisted copy: {path}" for path in unknown_patches
    )
    for entry in entries:
        destination = repo / entry.destination
        if not destination.is_file():
            errors.append(f"missing destination: {entry.destination}")
            continue
        actual_size = destination.stat().st_size
        actual_sha256 = digest(destination)
        patch = patches.get(entry.destination)
        expected_size = patch.size if patch else entry.size
        expected_sha256 = patch.sha256 if patch else entry.sha256
        if actual_size != expected_size or actual_sha256 != expected_sha256:
            errors.append(
                "destination differs from recorded copy/integration state: "
                f"{entry.destination} ({actual_size}, {actual_sha256})"
            )
    if errors:
        raise SystemExit("\n".join(errors))


def verify_target_integrations(repo: Path, entries: list[TargetIntegrationEntry]) -> None:
    errors: list[str] = []
    destinations = {entry.destination for entry in entries}
    if destinations != REQUIRED_TARGET_COMPATIBILITY_SHIMS:
        errors.append(
            "target integration manifest paths differ from required compatibility shims: "
            f"{sorted(destinations)}"
        )
    for entry in entries:
        destination = repo / entry.destination
        if entry.classification != TARGET_COMPATIBILITY_CLASSIFICATION:
            errors.append(f"invalid target integration classification: {entry.destination}")
        if not entry.rationale.strip():
            errors.append(f"missing target integration rationale: {entry.destination}")
        if not destination.is_file():
            errors.append(f"missing target integration file: {entry.destination}")
            continue
        actual_size = destination.stat().st_size
        actual_sha256 = digest(destination)
        if actual_size != entry.size or actual_sha256 != entry.sha256:
            errors.append(
                "target integration differs from recorded state: "
                f"{entry.destination} ({actual_size}, {actual_sha256})"
            )
    if errors:
        raise SystemExit("\n".join(errors))


def verify_cadmin_reference_allowlist(repo: Path) -> None:
    manifest_path = repo / CADMIN_REFERENCE_MANIFEST
    lines = manifest_path.read_text(encoding="utf-8").splitlines()
    expected_header = "path\tclassification\towner\tremoval_gate"
    if not lines or lines[0] != expected_header:
        raise SystemExit(f"invalid cadmin reference allowlist header: {manifest_path}")

    allowlisted: set[str] = set()
    errors: list[str] = []
    for line in lines[1:]:
        path, classification, owner, removal_gate = line.split("\t", 3)
        if path in allowlisted:
            errors.append(f"duplicate cadmin reference allowlist path: {path}")
        allowlisted.add(path)
        if not classification.strip() or not owner.strip() or not removal_gate.strip():
            errors.append(f"incomplete cadmin reference allowlist entry: {path}")
        if not (repo / path).is_file():
            errors.append(f"missing cadmin reference allowlist path: {path}")

    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    actual: set[str] = set()
    for raw_path in result.stdout.split(b"\0"):
        if not raw_path:
            continue
        path = raw_path.decode("utf-8")
        candidate = repo / path
        if not candidate.is_file():
            continue
        if "cadmin" in path.casefold() or b"cadmin" in candidate.read_bytes().lower():
            actual.add(path)

    errors.extend(f"unreviewed cadmin reference: {path}" for path in sorted(actual - allowlisted))
    errors.extend(
        f"stale cadmin reference allowlist entry: {path}" for path in sorted(allowlisted - actual)
    )
    if errors:
        raise SystemExit("\n".join(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=None)
    parser.add_argument("--write-manifest", action="store_true")
    parser.add_argument("--write-patch-manifest", action="store_true")
    repo = Path(__file__).resolve().parents[1]
    args = parser.parse_args()
    expected_commit, default_source = read_source_pin(repo)
    source_arg = args.source if args.source is not None else default_source
    source = source_arg if source_arg.is_absolute() else repo / source_arg
    manifest_path = repo / MANIFEST

    verify_source(source, expected_commit)
    expected = expected_entries(source)
    if args.write_manifest:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(render_manifest(expected), encoding="utf-8")

    recorded = read_manifest(manifest_path)
    if recorded != expected:
        raise SystemExit("manifest does not match the pinned source allowlist")
    patch_manifest_path = repo / PATCH_MANIFEST
    patches = read_patch_manifest(patch_manifest_path)
    if args.write_patch_manifest:
        current_patches = current_patch_entries(repo, recorded, patches)
        patch_manifest_path.write_text(
            render_patch_manifest(current_patches),
            encoding="utf-8",
        )
        patches = {entry.destination: entry for entry in current_patches}
    retired = retired_adoption_destinations(repo, (entry.destination for entry in recorded))
    active_recorded = [entry for entry in recorded if entry.destination not in retired]
    active_patches = {
        destination: patch for destination, patch in patches.items() if destination not in retired
    }
    verify_destinations(repo, active_recorded, active_patches)
    target_integrations = read_target_integration_manifest(repo / TARGET_INTEGRATION_MANIFEST)
    verify_target_integrations(repo, target_integrations)
    verify_cadmin_reference_allowlist(repo)
    print(
        f"verified {len(recorded)} copied files from {expected_commit} and "
        f"{len(target_integrations)} target-owned compatibility shims"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
