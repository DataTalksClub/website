from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ci.ownership import load_graph

ROOT = Path(__file__).resolve().parents[1]

# A deliberate full fallback needs a reviewed reason here; a new source directory must not
# inherit an exception from a filename or an old compatibility shim. An unmapped path selects
# the `unknown_path` full profile, so an entry here trades verification time for safety, never
# the reverse.
REVIEWED_DELIBERATELY_UNMAPPED: dict[str, str] = {
    "temporary": (
        "One-time ingest staging read only by scripts/prod/*, never by a public surface "
        "(_docs/architecture/database-only-content.md). It is deleted once production is "
        "ingested, so it gets no permanent owner node; until then a change to the staging "
        "snapshot falls back to full verification."
    ),
}


def _top_level_directories(root: Path = ROOT) -> set[str]:
    """Top-level directories of the repository's *tracked* content.

    Deliberately reads the git index rather than the working directory. A gitignored
    build output (`staticfiles/`, `.local/`), an untracked scratch directory, or a stale
    checkout leftover is not repository content and does not exist in CI's clean checkout;
    scanning the filesystem made this guard pass in CI while failing on a developer machine,
    which is exactly the disagreement a release gate must not have. The index -- not
    `HEAD` -- is what is read, so a newly `git add`-ed application is caught before it is
    ever committed.
    """
    listing = subprocess.run(
        ("git", "-C", str(root), "ls-files", "-z"),
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    directories = set()
    for entry in listing.split("\0"):
        head, separator, _ = entry.partition("/")
        if separator and head:
            directories.add(head)
    return directories


def _owned_top_level_directories() -> set[str]:
    return {
        prefix.split("/", 1)[0]
        for node in load_graph()["nodes"]
        if node["kind"] == "owner"
        for prefix in node["prefixes"]
        if prefix
    }


def _assert_reviewed_top_level_ownership(
    actual: set[str],
    owned: set[str],
    reviewed: dict[str, str],
) -> None:
    reviewed_names = set(reviewed)
    assert reviewed_names <= actual, (
        "Reviewed deliberately-unmapped directories are missing from the repository: "
        + ", ".join(sorted(reviewed_names - actual))
    )
    assert not (owned & reviewed_names), (
        "A top-level directory is both graph-owned and deliberately unmapped: "
        + ", ".join(sorted(owned & reviewed_names))
    )
    unreviewed = sorted(actual - owned - reviewed_names)
    assert not unreviewed, (
        "Unreviewed top-level directories: "
        + ", ".join(unreviewed)
        + ". Add an owner prefix to ci/ownership.json or add a reviewed full-fallback "
        "reason to REVIEWED_DELIBERATELY_UNMAPPED; do not let a new app silently fall through "
        "to unknown_path."
    )


def test_every_top_level_directory_has_reviewed_ownership_or_full_fallback() -> None:
    _assert_reviewed_top_level_ownership(
        _top_level_directories(),
        _owned_top_level_directories(),
        REVIEWED_DELIBERATELY_UNMAPPED,
    )


def test_new_unreviewed_top_level_directory_has_actionable_failure() -> None:
    with pytest.raises(AssertionError, match="new_app.*ci/ownership.json"):
        _assert_reviewed_top_level_ownership(
            {"api", "new_app"},
            {"api"},
            {},
        )


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(("git", "-C", str(repository), *arguments), check=True, capture_output=True)


def test_guard_reads_tracked_content_not_the_working_directory(tmp_path: Path) -> None:
    """Local-only noise must not decide a release gate, but a staged app must."""
    _git(tmp_path, "init", "-q")
    (tmp_path / ".gitignore").write_text("staticfiles/\n", encoding="utf-8")
    for directory in ("api", "staticfiles", "scratch"):
        (tmp_path / directory).mkdir()
        (tmp_path / directory / "file.py").write_text("", encoding="utf-8")
    _git(tmp_path, "add", ".gitignore", "api")

    # `staticfiles` is a gitignored build output and `scratch` is untracked: neither exists
    # in a clean CI checkout, so neither may make this guard disagree with CI.
    assert _top_level_directories(tmp_path) == {"api"}

    # A brand new application is caught as soon as it is staged, before any commit.
    _git(tmp_path, "add", "scratch")
    assert _top_level_directories(tmp_path) == {"api", "scratch"}


def test_reviewed_reasons_are_substantive() -> None:
    for name, reason in REVIEWED_DELIBERATELY_UNMAPPED.items():
        assert len(reason) > 40, f"{name} needs a reviewed reason, not a label"
