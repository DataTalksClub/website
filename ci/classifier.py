from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

from ci.provenance import build_provenance, dump_provenance
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
    dispatch_operation: str = "",
) -> dict[str, object]:
    safe_base = base if SHA_RE.fullmatch(base) else None
    safe_head = release_sha if SHA_RE.fullmatch(release_sha) else None

    def full(reason: str, *, use_base: str | None = None) -> dict[str, object]:
        return full_selection(
            event=event,
            base=safe_base if use_base is None else use_base,
            head=safe_head,
            reason=reason,
        )

    if event == "workflow_dispatch":
        if dispatch_operation != "promote" or not SHA_RE.fullmatch(release_sha):
            return full_selection(
                event=event,
                base=None,
                head=safe_head,
                reason="manual_dispatch",
            )
        if not _exact_commit(repository, release_sha):
            return full_selection(
                event=event,
                base=None,
                head=safe_head,
                reason="manual_dispatch",
            )
        parent = _first_parent(repository, release_sha)
        if parent is None:
            return full_selection(
                event=event,
                base=None,
                head=safe_head,
                reason="manual_dispatch",
            )
        return _classify_range(
            repository,
            event=event,
            base=parent,
            release_sha=release_sha,
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
    return _classify_range(
        repository,
        event=event,
        base=base,
        release_sha=release_sha,
    )


def _classify_range(
    repository: Path,
    *,
    event: str,
    base: str,
    release_sha: str,
) -> dict[str, object]:
    def full(reason: str) -> dict[str, object]:
        return full_selection(event=event, base=base, head=release_sha, reason=reason)

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


def _first_parent(repository: Path, revision: str) -> str | None:
    result = _git(repository, "rev-parse", "--verify", f"{revision}^1", check=False)
    if result.returncode != 0:
        return None
    parent = result.stdout.strip().decode("ascii", errors="replace")
    if not SHA_RE.fullmatch(parent) or not _exact_commit(repository, parent):
        return None
    return parent


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


def _append_outputs(
    path: str | None,
    selection: dict[str, object],
    *,
    provenance: dict[str, object] | None = None,
) -> None:
    if path:
        with Path(path).open("a", encoding="utf-8") as output:
            output.write(f"profile={selection['profile']}\n")
            output.write(f"reason={selection['reason']}\n")
            if provenance is not None:
                output.write(f"artifact_name={provenance['artifact_name']}\n")
                output.write(f"created_attempt={provenance['created_attempt']}\n")
                output.write(f"run_id={provenance['run_id']}\n")
                output.write(f"selection_sha256={provenance['selection_sha256']}\n")


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
    select.add_argument("--run-id")
    select.add_argument("--created-attempt", type=int)
    select.add_argument("--controller-sha")
    select.add_argument("--source-after-sha")
    select.add_argument("--source-before-sha")
    select.add_argument("--provenance-output")
    select.add_argument("--dispatch-operation", default="")
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
        dispatch_operation=args.dispatch_operation,
    )
    dump_selection(selection, args.output)
    provenance = None
    provenance_arguments = (
        args.run_id,
        args.created_attempt,
        args.controller_sha,
        args.provenance_output,
    )
    if any(value is not None for value in provenance_arguments):
        if not all(value is not None for value in provenance_arguments):
            parser.error("provenance output requires run, attempt, controller, and output")
        provenance = build_provenance(
            load_selection(args.output),
            run_id=args.run_id,
            created_attempt=args.created_attempt,
            controller_sha=args.controller_sha,
            release_sha=args.release_sha,
            source_after_sha=args.source_after_sha or None,
            source_before_sha=args.source_before_sha or None,
        )
        dump_provenance(provenance, args.provenance_output)
    summary = selection_summary(selection)
    _write_summary(args.summary, summary)
    _append_outputs(args.github_output, selection, provenance=provenance)


if __name__ == "__main__":
    main()
