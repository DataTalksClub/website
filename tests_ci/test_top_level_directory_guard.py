from __future__ import annotations

from pathlib import Path

import pytest

from ci.ownership import load_graph

ROOT = Path(__file__).resolve().parents[1]
LOCAL_ONLY_DIRECTORIES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tmp",
        ".venv",
        "__pycache__",
        "node_modules",
    }
)

# This is intentionally explicit and currently empty. A deliberate full fallback needs a
# reviewed reason here; a new source directory must not inherit an exception from a filename or
# an old compatibility shim.
REVIEWED_DELIBERATELY_UNMAPPED: dict[str, str] = {}


def _top_level_directories(root: Path = ROOT) -> set[str]:
    return {
        path.name
        for path in root.iterdir()
        if path.is_dir() and path.name not in LOCAL_ONLY_DIRECTORIES
    }


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
