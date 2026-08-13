#!/usr/bin/env python3
"""Validate the point-in-time Milestone-0 readiness matrix.

The audit is deliberately stored as Markdown so people can review it in GitHub.  This
small validator keeps the control-plane fields machine-checkable without adding a
documentation parser or a network dependency.  It validates the captured snapshot;
it never fetches GitHub or changes repository state.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

REPOSITORY = "DataTalksClub/website"
AUDIT_PATH = Path("_docs/audits/2026-08-14-milestone-0-readiness.md")
EXPECTED_ISSUES = frozenset(range(12, 30)) | frozenset({30, 34})
EXPECTED_TITLES = {
    12: "Decision: Confirm MVP GitHub-backed editorial workflow",
    13: "Decision: Record adoption—not rewrite—of the existing course platform",
    14: "Decision: Confirm cohort-owned curriculum for the first consolidation release",
    15: "Decision: Approve the legacy course-edition family mapping",
    16: "Decision: Inventory course API consumers before legacy-host redirects",
    17: "Decision: Confirm verified-email registration semantics",
    18: "Decision: Keep capacity and waitlists out of the MVP",
    19: "Decision: Confirm legacy timezone interpretation",
    20: "Decision: Select the staff OIDC provider and break-glass policy",
    21: "Integrate Relay for transactional email and retire Datamailer",
    22: "Decision: Confirm the MVP transactional email purpose catalog",
    23: "Decision: Approve privacy ownership, retention, and minors policy",
    24: "Decision: Use PostgreSQL search while preserving public search contracts",
    25: "Decision: Approve the cost-aware sandbox network topology",
    26: "Decision: Approve service levels, recovery targets, and alert ownership",
    27: "Decision: Approve analytics and tracking preservation policy",
    28: "Decision: Confirm high-risk action approvals and reauthentication",
    29: "Decision: Freeze production cutover scope to preservation-first",
    30: "Copy the existing course platform verbatim and establish its characterization baseline",
    34: "Build the legacy URL, link, fragment, asset, and SEO manifest crawler",
}
EXPECTED_REF = "refs/heads/main"
STATUS_VALUES = frozenset({"evidence-complete", "partial", "missing", "blocked"})
GATE_VALUES = frozenset({"decision", "implementation", "human", "evidence"})
REQUIRED_COLUMNS = (
    "Issue",
    "Title",
    "Priority / labels",
    "State",
    "Direct dependencies (snapshot state)",
    "Gate class",
    "Status",
    "Evidence (role and limitation)",
    "Evidence observed at (UTC)",
    "Next safe action",
    "Downstream issue / gate",
)
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
UTC_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
ISSUE_CELL_PATTERN = re.compile(
    r"^\[#(?P<number>\d+)\]\(https://github\.com/"
    rf"{re.escape(REPOSITORY)}/issues/(?P<url_number>\d+)\)$"
)
ISSUE_REFERENCE_PATTERN = re.compile(r"#(?P<number>\d+)")
ISSUE_STATE_PATTERN = re.compile(r"#(?P<number>\d+)\s+(?P<state>OPEN|CLOSED)\b")
LINK_PATTERN = re.compile(r"\[[^\]]+\]\((?P<target>[^)]+)\)")
ISSUE_COMMENT_URL_PATTERN = re.compile(
    rf"^https://github\.com/{re.escape(REPOSITORY)}/issues/\d+#issuecomment-\d+$"
)


class ValidationError(ValueError):
    """Raised when the captured Markdown does not satisfy the audit contract."""


@dataclass(frozen=True)
class MatrixRow:
    issue_number: int
    cells: tuple[str, ...]


def _split_table_row(line: str) -> list[str]:
    value = line.strip()
    if not value.startswith("|"):
        raise ValidationError(f"matrix row must start with '|': {line!r}")
    value = value[1:]
    if value.endswith("|"):
        value = value[:-1]
    return [cell.strip() for cell in value.split("|")]


def _metadata_value(lines: list[str], label: str) -> str:
    prefix = f"- {label}: `"
    for line in lines:
        if line.startswith(prefix) and line.endswith("`"):
            return line[len(prefix) : -1]
    raise ValidationError(f"missing snapshot metadata: {label}")


def _parse_utc(value: str, label: str) -> datetime:
    if value.startswith("`") and value.endswith("`"):
        value = value[1:-1]
    if not UTC_PATTERN.fullmatch(value):
        raise ValidationError(f"{label} must be an explicit UTC timestamp ending in Z: {value!r}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"{label} is not a valid UTC timestamp: {value!r}") from exc
    if parsed.utcoffset() is None:
        raise ValidationError(f"{label} must be timezone-aware: {value!r}")
    return parsed


def _validate_metadata(lines: list[str]) -> None:
    repository = _metadata_value(lines, "Snapshot repository")
    if repository != REPOSITORY:
        raise ValidationError(f"snapshot repository must be {REPOSITORY!r}, got {repository!r}")

    ref = _metadata_value(lines, "Snapshot ref")
    if ref != EXPECTED_REF:
        raise ValidationError(f"snapshot ref must be {EXPECTED_REF!r}, got {ref!r}")

    sha = _metadata_value(lines, "Snapshot SHA")
    if not SHA_PATTERN.fullmatch(sha):
        raise ValidationError(f"snapshot SHA must be a 40-character lowercase hex SHA: {sha!r}")

    timestamps = {
        label: _parse_utc(_metadata_value(lines, label), label)
        for label in (
            "Snapshot at (UTC)",
            "GitHub metadata observed at (UTC)",
            "Repository provenance observed at (UTC)",
        )
    }
    if len(set(timestamps.values())) != len(timestamps):
        raise ValidationError("snapshot and provenance timestamps must be distinct")


def _repository_path_from_link(target: str, audit_path: Path) -> Path | None:
    """Resolve a relative repository link, returning None for an allowed issue comment."""

    if ISSUE_COMMENT_URL_PATTERN.fullmatch(target):
        return None
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc:
        raise ValidationError(
            f"evidence link must be a repository path or issue comment: {target!r}"
        )
    relative = unquote(parsed.path)
    if not relative:
        raise ValidationError(f"evidence link has no path: {target!r}")
    root = Path.cwd().resolve()
    resolved = (audit_path.parent / relative).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValidationError(f"evidence link escapes the repository: {target!r}") from exc
    return resolved


def _validate_evidence(cell: str, audit_path: Path) -> None:
    links = [match.group("target") for match in LINK_PATTERN.finditer(cell)]
    if not links:
        raise ValidationError("each row needs at least one linked evidence item")
    for target in links:
        path = _repository_path_from_link(target, audit_path)
        if path is not None and not path.is_file():
            raise ValidationError(f"evidence path does not exist: {target!r} ({path})")


def _validate_dependency_states(cell: str) -> None:
    references = [int(match.group("number")) for match in ISSUE_REFERENCE_PATTERN.finditer(cell)]
    if not references:
        if "None recorded" not in cell:
            raise ValidationError(
                "a dependency cell without issue references must explicitly say 'None recorded'"
            )
        return
    for number in references:
        state_match = ISSUE_STATE_PATTERN.search(cell)
        if state_match is None or int(state_match.group("number")) != number:
            raise ValidationError(
                f"dependency #{number} must include its captured OPEN/CLOSED state in the same cell"
            )
        # A cell can contain several references; remove the already-validated match and continue.
        cell = cell[: state_match.start()] + cell[state_match.end() :]


def _validate_gate_class(cell: str, row_number: int) -> None:
    values = [value.strip().lower() for value in cell.split(";") if value.strip()]
    if not values or any(value not in GATE_VALUES for value in values):
        allowed = ", ".join(sorted(GATE_VALUES))
        raise ValidationError(
            f"row #{row_number} gate class must contain only semicolon-separated values: {allowed}"
        )
    if "human" in values and "HUMAN" not in cell.upper():
        raise ValidationError(f"row #{row_number} HUMAN gate must remain explicit")


def _matrix_rows(lines: list[str]) -> list[MatrixRow]:
    try:
        heading_index = lines.index("## Readiness matrix")
    except ValueError as exc:
        raise ValidationError("missing '## Readiness matrix' section") from exc

    header_index = next(
        (index for index in range(heading_index + 1, len(lines)) if lines[index].startswith("|")),
        None,
    )
    if header_index is None:
        raise ValidationError("readiness matrix has no Markdown table")
    headers = _split_table_row(lines[header_index])
    if tuple(headers) != REQUIRED_COLUMNS:
        raise ValidationError(f"unexpected matrix columns: {headers!r}")
    if header_index + 1 >= len(lines) or not lines[header_index + 1].startswith("|"):
        raise ValidationError("readiness matrix is missing its separator row")

    rows: list[MatrixRow] = []
    for line in lines[header_index + 2 :]:
        if not line.startswith("|"):
            break
        cells = _split_table_row(line)
        if len(cells) != len(REQUIRED_COLUMNS):
            raise ValidationError(
                f"matrix row has {len(cells)} columns, expected {len(REQUIRED_COLUMNS)}"
            )
        issue_match = ISSUE_CELL_PATTERN.fullmatch(cells[0])
        if issue_match is None or issue_match.group("number") != issue_match.group("url_number"):
            raise ValidationError(f"invalid checked issue link: {cells[0]!r}")
        rows.append(MatrixRow(int(issue_match.group("number")), tuple(cells)))
    return rows


def validate_text(text: str, path: Path = AUDIT_PATH) -> list[MatrixRow]:
    """Validate Markdown *text* using *path* as the repository-relative evidence anchor."""

    lines = text.splitlines()
    _validate_metadata(lines)
    rows = _matrix_rows(lines)
    numbers = [row.issue_number for row in rows]
    if len(rows) != len(EXPECTED_ISSUES):
        raise ValidationError(
            f"matrix must contain exactly {len(EXPECTED_ISSUES)} rows, found {len(rows)}"
        )
    if len(numbers) != len(set(numbers)):
        raise ValidationError("matrix contains duplicate issue numbers")
    if set(numbers) != EXPECTED_ISSUES:
        raise ValidationError(
            "matrix issue coverage mismatch: "
            f"expected {sorted(EXPECTED_ISSUES)}, found {sorted(numbers)}"
        )

    for row in rows:
        cells = row.cells
        expected_title = EXPECTED_TITLES[row.issue_number]
        if cells[1] != expected_title:
            raise ValidationError(
                f"row #{row.issue_number} title does not match the captured GitHub title: "
                f"expected {expected_title!r}, found {cells[1]!r}"
            )
        if not cells[2] or cells[3] not in {"OPEN", "CLOSED"}:
            raise ValidationError(
                f"row #{row.issue_number} is missing title, labels, or OPEN/CLOSED state"
            )
        _validate_dependency_states(cells[4])
        _validate_gate_class(cells[5], row.issue_number)
        if cells[6] not in STATUS_VALUES:
            raise ValidationError(
                f"row #{row.issue_number} has invalid status {cells[6]!r}; "
                f"expected one of {sorted(STATUS_VALUES)}"
            )
        _validate_evidence(cells[7], path)
        _parse_utc(cells[8], f"row #{row.issue_number} evidence timestamp")
        if not cells[9] or not cells[10]:
            raise ValidationError(
                f"row #{row.issue_number} needs a next action and downstream gate"
            )
    return rows


def validate(path: Path = AUDIT_PATH) -> list[MatrixRow]:
    """Validate *path* and return its parsed rows."""

    if not path.is_file():
        raise ValidationError(f"audit file does not exist: {path}")
    return validate_text(path.read_text(encoding="utf-8"), path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, default=AUDIT_PATH)
    args = parser.parse_args(argv)
    try:
        rows = validate(args.path)
    except ValidationError as exc:
        print(f"milestone-0 readiness validation failed: {exc}", file=sys.stderr)
        return 1
    print(f"validated {args.path}: {len(rows)} rows, issue coverage #12-#29, #30, #34")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
