"""Repository guard: an unresolved merge conflict must never reach a tracked file.

`_docs/runbooks/production-data-migration.md` carried literal conflict markers on `main`
for long enough that its rehearsal sequence told the reader a step that exists "does not
exist" (#325). Nothing failed, because nothing looked. This looks.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Assembled from repeated characters rather than written out. A guard that has to exempt
# its own path from its own scan has a hole shaped exactly like itself; this file simply
# never contains a marker, so no exemption -- and no path list -- is needed.
START = "<" * 7
MERGE_BASE = "|" * 7
SEPARATOR = "=" * 7
END = ">" * 7

# `git grep -I` skips binary blobs using git's own heuristic, so a PNG or a SQLite fixture
# that happens to contain these bytes is never decoded or reported. Only `START`/`END` are
# searched: they cannot occur in prose by accident, whereas a bare `=======` is a perfectly
# ordinary Markdown setext underline. The separator and the diff3 base marker are resolved
# in the second pass, where "inside an open conflict region" is knowable.
_CANDIDATE_PATTERN = rf"^({re.escape(START)}|{re.escape(END)})( |$)"


def _candidate_files(root: Path = ROOT) -> list[str]:
    """Tracked text files carrying a conflict start or end marker at the line start.

    Reads git's view of the repository, not the filesystem: a gitignored build output, an
    untracked scratch file under `.tmp/`, or a sibling agent worktree is not repository
    content and must not be able to fail -- or to silently pass -- a release gate. One C
    speed `git grep` over the whole tree replaces a path list that would rot, and the
    Python pass below then reads only the handful of files it names.
    """
    completed = subprocess.run(
        ("git", "-C", str(root), "grep", "--no-color", "-I", "-l", "-z", "-E", _CANDIDATE_PATTERN),
        capture_output=True,
        text=True,
    )
    if completed.returncode == 1 and not completed.stderr.strip():
        return []
    if completed.returncode != 0:
        raise AssertionError(f"git grep failed in {root}: {completed.stderr.strip()}")
    return [entry for entry in completed.stdout.split("\0") if entry]


def _conflict_markers(root: Path, path: str) -> list[tuple[int, str]]:
    """Every conflict marker line in one file, as `(line number, line)`."""
    text = (root / path).read_text(encoding="utf-8", errors="replace")
    findings: list[tuple[int, str]] = []
    inside = False
    for number, line in enumerate(text.splitlines(), start=1):
        if line == START or line.startswith(f"{START} "):
            findings.append((number, line))
            inside = True
        elif line == END or line.startswith(f"{END} "):
            findings.append((number, line))
            inside = False
        elif inside and (
            line == SEPARATOR or line == MERGE_BASE or line.startswith(f"{MERGE_BASE} ")
        ):
            findings.append((number, line))
    return findings


def _conflicts(root: Path = ROOT) -> dict[str, list[tuple[int, str]]]:
    found = {path: _conflict_markers(root, path) for path in _candidate_files(root)}
    return {path: markers for path, markers in found.items() if markers}


def _assert_no_conflict_markers(conflicts: dict[str, list[tuple[int, str]]]) -> None:
    assert not conflicts, (
        "Unresolved merge conflict markers in tracked files:\n"
        + "\n".join(
            f"  {path}:{number}: {line}"
            for path, markers in sorted(conflicts.items())
            for number, line in markers
        )
        + "\nResolve the conflict and delete every marker before committing."
    )


def test_no_tracked_file_contains_a_merge_conflict_marker() -> None:
    _assert_no_conflict_markers(_conflicts())


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(("git", "-C", str(repository), *arguments), check=True, capture_output=True)


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    (tmp_path / ".gitignore").write_text(".tmp/\n", encoding="utf-8")
    (tmp_path / "clean.md").write_text("no conflict here\n", encoding="utf-8")
    _git(tmp_path, "add", ".gitignore", "clean.md")
    return tmp_path


def _plant(repository: Path, name: str) -> None:
    (repository / name).write_text(
        f"before\n{START} HEAD\nours\n{SEPARATOR}\ntheirs\n{END} branch\nafter\n",
        encoding="utf-8",
    )


def test_a_planted_conflict_is_reported_with_all_markers_and_line_numbers(
    repository: Path,
) -> None:
    _plant(repository, "runbook.md")
    _git(repository, "add", "runbook.md")

    assert _conflicts(repository) == {
        "runbook.md": [
            (2, f"{START} HEAD"),
            (4, SEPARATOR),
            (6, f"{END} branch"),
        ]
    }

    with pytest.raises(AssertionError, match=r"runbook\.md:2"):
        _assert_no_conflict_markers(_conflicts(repository))


def test_diff3_merge_base_marker_is_reported(repository: Path) -> None:
    (repository / "runbook.md").write_text(
        f"{START} HEAD\nours\n{MERGE_BASE} base\nbase\n{SEPARATOR}\ntheirs\n{END} branch\n",
        encoding="utf-8",
    )
    _git(repository, "add", "runbook.md")

    assert [number for number, _ in _conflicts(repository)["runbook.md"]] == [1, 3, 5, 7]


def test_untracked_and_ignored_files_are_not_scanned(repository: Path) -> None:
    """Local-only noise -- scratch files, agent worktrees -- must not decide a release gate."""
    (repository / ".tmp").mkdir()
    _plant(repository, ".tmp/scratch.md")
    _plant(repository, "untracked.md")

    assert _conflicts(repository) == {}

    # ...but the moment the file is staged, before any commit, the guard sees it.
    _git(repository, "add", "-f", "untracked.md")
    assert list(_conflicts(repository)) == ["untracked.md"]


def test_binary_files_are_neither_decoded_nor_reported(repository: Path) -> None:
    """A fixture database or a PNG may contain these bytes; it is not a merge conflict."""
    (repository / "fixture.bin").write_bytes(
        b"\x00\x01\x02\n" + f"{START} HEAD\n".encode() + b"\x00\xff\xfe\n"
    )
    _git(repository, "add", "fixture.bin")

    assert _candidate_files(repository) == []
    assert _conflicts(repository) == {}


def test_a_markdown_setext_heading_is_not_a_conflict_marker(repository: Path) -> None:
    """`=======` under a line of prose is an ordinary H1 underline, not half a conflict."""
    (repository / "heading.md").write_text(f"Runbook\n{SEPARATOR}\n\nBody.\n", encoding="utf-8")
    _git(repository, "add", "heading.md")

    assert _conflicts(repository) == {}


def test_the_guard_reads_only_the_files_that_matched(repository: Path) -> None:
    """The scan is one `git grep` plus a read of its hits, so it stays a quality-tier check."""
    for index in range(50):
        (repository / f"file{index}.md").write_text("prose\n", encoding="utf-8")
    _plant(repository, "runbook.md")
    _git(repository, "add", ".")

    assert _candidate_files(repository) == ["runbook.md"]
