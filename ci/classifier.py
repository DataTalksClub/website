from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

from ci.selection import (
    SHA_RE,
    ChangeRecord,
    DiffParseError,
    UnsupportedStatus,
    classify_records,
    dump_selection,
    full_selection,
    load_selection,
    parse_name_status,
    selection_summary,
)

ZERO_SHA = "0" * 40
ORDINARY_MODES = {"100644", "100755"}


def classify_git_change(
    *,
    repository: Path,
    event: str,
    base: str,
    after: str,
    github_sha: str,
    release_sha: str,
) -> dict[str, object]:
    safe_base = base if SHA_RE.fullmatch(base) else None
    safe_head = release_sha if SHA_RE.fullmatch(release_sha) else None

    def full(reason: str) -> dict[str, object]:
        return full_selection(event=event, base=safe_base, head=safe_head, reason=reason)

    if event == "workflow_dispatch":
        return full_selection(
            event=event,
            base=None,
            head=safe_head,
            reason="manual_dispatch",
        )
    if event != "push":
        return full("head_invalid")
    if not SHA_RE.fullmatch(release_sha) or release_sha == ZERO_SHA:
        return full("head_invalid")
    if after != github_sha or github_sha != release_sha:
        return full("head_mismatch")
    if base == ZERO_SHA:
        return full("base_zero")
    if not SHA_RE.fullmatch(base):
        return full("base_invalid")
    if not _exact_commit(repository, release_sha):
        return full("head_unavailable")
    if not _exact_commit(repository, base):
        return full("base_unavailable")

    ancestor = _git(repository, "merge-base", "--is-ancestor", base, release_sha, check=False)
    if ancestor.returncode == 1:
        return full("non_ancestor_base")
    if ancestor.returncode != 0:
        return full("base_unavailable")

    diff = _git(
        repository,
        "diff",
        "--name-status",
        "-z",
        "--find-renames",
        "--find-copies",
        base,
        release_sha,
        check=False,
    )
    if diff.returncode != 0:
        return full("diff_failed")
    if not diff.stdout:
        return full("diff_empty")
    try:
        records = parse_name_status(diff.stdout)
    except UnsupportedStatus:
        return full("unsupported_status")
    except DiffParseError:
        return full("diff_unparseable")
    if not records:
        return full("diff_empty")

    try:
        base_tree = _tree_modes(repository, base)
        head_tree = _tree_modes(repository, release_sha)
    except (OSError, subprocess.SubprocessError, ValueError):
        return full("unsupported_file_mode")
    checked_records = tuple(
        ChangeRecord(record.status, record.paths, _ordinary_record(record, base_tree, head_tree))
        for record in records
    )
    return classify_records(
        checked_records,
        event=event,
        base=base,
        head=release_sha,
    )


def _ordinary_record(
    record: ChangeRecord,
    base_tree: dict[str, str],
    head_tree: dict[str, str],
) -> bool:
    status = record.status[0]
    entries: tuple[tuple[dict[str, str], str], ...]
    if status == "A":
        entries = ((head_tree, record.paths[0]),)
    elif status == "D":
        entries = ((base_tree, record.paths[0]),)
    elif status == "M":
        entries = ((base_tree, record.paths[0]), (head_tree, record.paths[0]))
    else:
        entries = ((base_tree, record.paths[0]), (head_tree, record.paths[1]))
    return all(tree.get(path) in ORDINARY_MODES for tree, path in entries)


def _tree_modes(repository: Path, revision: str) -> dict[str, str]:
    result = _git(repository, "ls-tree", "-r", "-z", revision)
    modes: dict[str, str] = {}
    for entry in result.stdout[:-1].split(b"\0") if result.stdout else ():
        metadata, separator, raw_path = entry.partition(b"\t")
        if not separator:
            raise ValueError("invalid ls-tree record")
        fields = metadata.split(b" ")
        if len(fields) != 3:
            raise ValueError("invalid ls-tree metadata")
        mode, object_type, _object_id = fields
        if object_type != b"blob":
            stored_mode = "non_blob"
        else:
            stored_mode = mode.decode("ascii")
        path = raw_path.decode("utf-8", errors="surrogateescape")
        modes[path] = stored_mode
    return modes


def _exact_commit(repository: Path, revision: str) -> bool:
    result = _git(repository, "rev-parse", "--verify", f"{revision}^{{commit}}", check=False)
    return result.returncode == 0 and result.stdout.strip() == revision.encode("ascii")


def _git(
    repository: Path, *arguments: str, check: bool = True
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", os.fspath(repository), *arguments],
        check=check,
        capture_output=True,
    )


def _write_summary(path: str | None, summary: str) -> None:
    if path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(summary, encoding="utf-8")


def _append_outputs(path: str | None, selection: dict[str, object]) -> None:
    if path:
        with Path(path).open("a", encoding="utf-8") as output:
            output.write(f"profile={selection['profile']}\n")
            output.write(f"reason={selection['reason']}\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    select = subparsers.add_parser("select")
    select.add_argument("--repository", type=Path, default=Path.cwd())
    select.add_argument("--event", required=True)
    select.add_argument("--base", default="")
    select.add_argument("--after", default="")
    select.add_argument("--github-sha", required=True)
    select.add_argument("--release-sha", required=True)
    select.add_argument("--output", required=True)
    select.add_argument("--summary")
    select.add_argument("--github-output")
    validate = subparsers.add_parser("validate")
    validate.add_argument("--input", required=True)
    args = parser.parse_args()

    if args.command == "validate":
        load_selection(args.input)
        return
    selection = classify_git_change(
        repository=args.repository,
        event=args.event,
        base=args.base,
        after=args.after,
        github_sha=args.github_sha,
        release_sha=args.release_sha,
    )
    dump_selection(selection, args.output)
    summary = selection_summary(selection)
    _write_summary(args.summary, summary)
    _append_outputs(args.github_output, selection)


if __name__ == "__main__":
    main()
