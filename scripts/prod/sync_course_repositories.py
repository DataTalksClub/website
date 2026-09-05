#!/usr/bin/env python3
"""Pull entry point: load registered course repositories from local checkouts.

Git-synchronized -- see ``scripts/prod/__init__.py`` for what the two sync
models mean.  Content reaches this site two ways and they share one
implementation.  CI/CD *pushes* -- a course repository posts a signed push
event and the webhook enqueues a durable job that downloads the commit
archive.  A developer *pulls* -- this script reads checkouts that are already
on disk.  Both then call the same
:func:`content_sync.course_repository_ingest.ingest_course_repository`, so the
validation is not two implementations that could drift.

This script makes no network call.  The commit and the branch come from the
checkout's own git metadata, and the snapshot is ``git archive HEAD`` -- the
same kind of tar codeload serves the push route, so a repository's
``.gitattributes`` applies here exactly as it does there.

Which repositories exist is data, not code.  This iterates the registered
``ContentSource`` rows, so adding a course means registering a source with
``scripts/prod/sync_course_repository_sources.py`` or ``manage.py
register_course_repository``, not editing a list here or in the Makefile.

    uv run --frozen python scripts/prod/sync_course_repositories.py \\
        --database .tmp/local.sqlite3 --from-disk .tmp/course-checkouts
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.prod.target import add_target_arguments, configure_target  # noqa: E402

SYNC_MODEL = "git-synchronized"
# It owns module and unit curricula: given at least one registered source and a
# checkout for it, this writes cohorts into a database that had none of its own.
BOOTSTRAPS_EMPTY_DATABASE = True

GIT_TIMEOUT_SECONDS = 30


class SyncCourseRepositoriesError(RuntimeError):
    """A bounded refusal to pull one or more course repositories.

    ``report`` carries the summaries for every source that succeeded before
    the refusal, so a partial run's results are never silently discarded.
    """

    def __init__(self, message: str, *, report: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.report = report


# --------------------------------------------------------------------------
# Source selection
# --------------------------------------------------------------------------


def select_sources(
    stable_ids: Sequence[str], *, explicit: Mapping[str, Path], root: Path | None
) -> list[Any]:
    from content.models import ContentSource
    from content_sync.course_repository_webhook import COURSE_REPOSITORY_ADAPTER_TYPE

    registered = list(
        ContentSource.objects.filter(
            enabled=True,
            adapter_type=COURSE_REPOSITORY_ADAPTER_TYPE,
        ).order_by("stable_id")
    )
    if not registered:
        raise SyncCourseRepositoriesError(
            "no enabled course-repository sources are registered. Register the pinned "
            "sources with `scripts/prod/sync_course_repository_sources.py`, or one "
            "source with `manage.py register_course_repository --stable-id ... "
            "--owner ... --repository ... --enabled`."
        )
    selection = list(stable_ids)
    if not selection and explicit and root is None:
        # Naming the only checkout there is names the run. Without this the run
        # went on to attempt every other registered source, found no checkout for
        # any of them, and exited non-zero on a request that was completely well
        # formed. With --from-disk the explicit entry is an override of one
        # source among many, so it does not narrow anything.
        selection = sorted(explicit)
    if not selection:
        return registered
    # Report the unmatched selection rather than "nothing to do": a typo must not
    # look like a clean run.
    missing = sorted(set(selection) - {source.stable_id for source in registered})
    if missing:
        raise SyncCourseRepositoriesError(f"not registered or not enabled: {', '.join(missing)}")
    return [source for source in registered if source.stable_id in set(selection)]


def parse_explicit_checkouts(entries: Sequence[str]) -> dict[str, Path]:
    checkouts: dict[str, Path] = {}
    for entry in entries:
        stable_id, separator, raw_path = entry.partition("=")
        if not separator or not stable_id or not raw_path:
            raise SyncCourseRepositoriesError(f"--checkout expects STABLE_ID=PATH, got {entry!r}")
        checkouts[stable_id] = Path(raw_path).expanduser()
    return checkouts


def checkout_for(source: Any, *, root: Path | None, explicit: Mapping[str, Path]) -> Path:
    chosen = explicit.get(source.stable_id)
    if chosen is not None:
        return chosen
    if root is None:
        raise SyncCourseRepositoriesError(
            f"no checkout for {source.stable_id}: pass --from-disk or "
            f"--checkout {source.stable_id}=PATH"
        )
    for name in (source.stable_id, source.repository_name):
        candidate = root / name
        if candidate.is_dir():
            return candidate
    raise SyncCourseRepositoriesError(
        f"no checkout for {source.stable_id}: expected "
        f"{root / source.stable_id} or {root / source.repository_name}"
    )


def checkout_plan(
    sources: Sequence[Any], *, root: Path | None, explicit: Mapping[str, Path]
) -> list[tuple[str, str, str, Path]]:
    """The registered sources and the checkout each would be read from."""

    plan: list[tuple[str, str, str, Path]] = []
    for source in sources:
        try:
            target = checkout_for(source, root=root, explicit=explicit)
        except SyncCourseRepositoriesError:
            # Nothing is there yet, so report where a clone should land.
            target = (root or Path(".")) / source.stable_id
        plan.append(
            (
                source.stable_id,
                f"{source.repository_owner}/{source.repository_name}",
                source.branch,
                target,
            )
        )
    return plan


# --------------------------------------------------------------------------
# Offline git reads
# --------------------------------------------------------------------------


def _git(root: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SyncCourseRepositoriesError(f"git is unavailable for {root}") from error
    if completed.returncode != 0:
        raise SyncCourseRepositoriesError(
            f"git {' '.join(arguments)} failed in {root}: "
            f"{completed.stderr.strip() or completed.returncode}"
        )
    return completed.stdout


def describe_checkout(
    source: Any,
    checkout: Path,
    *,
    allow_modified: bool,
    require_public_commit: bool,
) -> tuple[str, list[str]]:
    """Return the commit to ingest and the waivers that were used."""

    from content_sync.course_repository_checkout import commit_is_public

    if not checkout.is_dir():
        raise SyncCourseRepositoriesError(f"{source.stable_id}: {checkout} is not a directory")
    commit_sha = _git(checkout, "rev-parse", "HEAD").strip()
    branch = _git(checkout, "rev-parse", "--abbrev-ref", "HEAD").strip()
    dirty = _git(checkout, "status", "--porcelain=v1", "--untracked-files=no").strip()

    waivers: list[str] = []
    if branch != source.branch:
        message = (
            f"{source.stable_id}: {checkout} is on {branch!r} but the source is "
            f"registered for {source.branch!r}"
        )
        if not allow_modified:
            raise SyncCourseRepositoriesError(
                f"{message}; check the branch out or pass --allow-modified-checkout"
            )
        waivers.append(f"branch {branch!r} instead of {source.branch!r}")
    if dirty:
        # porcelain v1 is "XY<space>PATH"; the status columns may be blank.
        changed = [line[2:].lstrip() for line in dirty.splitlines()][:5]
        message = (
            f"{source.stable_id}: {checkout} has uncommitted changes "
            f"({', '.join(changed)}{', ...' if len(dirty.splitlines()) > 5 else ''})"
        )
        if not allow_modified:
            raise SyncCourseRepositoriesError(
                f"{message}; commit or stash them, or pass --allow-modified-checkout"
            )
        waivers.append(f"{len(dirty.splitlines())} uncommitted change(s), which are NOT imported")
    if require_public_commit and not commit_is_public(
        checkout,
        owner=source.repository_owner,
        name=source.repository_name,
        commit_sha=commit_sha,
    ):
        raise SyncCourseRepositoriesError(
            f"{source.stable_id}: {commit_sha} is not on a branch of "
            f"https://github.com/{source.repository_owner}/{source.repository_name} "
            f"in {checkout}; every source link the import publishes would 404. "
            f"Push it, or refresh the checkout with `make content-checkouts`."
        )
    return commit_sha, waivers


# --------------------------------------------------------------------------
# Pull
# --------------------------------------------------------------------------


def pull(
    *,
    sources: Sequence[Any],
    checkouts: Mapping[str, Path] | None = None,
    root: Path | None = None,
    allow_modified_checkout: bool = False,
    require_public_commit: bool = False,
    narrate: Callable[[str], None] | None = None,
    warn: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Pull every given source from its local checkout.

    One unusable checkout must not hide what the rest would have done: every
    source is attempted (unless it is the only one given, in which case its
    refusal is raised immediately) and the aggregate failure is raised last,
    with the summaries for every source that did succeed attached as
    ``error.report``.
    """

    from content_sync.course_repository_ingest import (
        CourseRepositoryIngestError,
        CourseRepositoryIngestResult,
        ingest_course_repository,
    )

    explicit = checkouts or {}
    results: list[CourseRepositoryIngestResult] = []
    failures: list[str] = []
    single = len(sources) == 1
    for source in sources:
        try:
            checkout = checkout_for(source, root=root, explicit=explicit)
            commit_sha, waivers = describe_checkout(
                source,
                checkout,
                allow_modified=allow_modified_checkout,
                require_public_commit=require_public_commit,
            )
        except SyncCourseRepositoriesError as error:
            if single:
                raise
            failures.append(source.stable_id)
            if warn is not None:
                warn(f"  SKIPPED [{source.stable_id}]: {error}")
            continue
        for waiver in waivers:
            if warn is not None:
                warn(
                    f"  waived for {source.stable_id}: {waiver}; the import records "
                    f"and imports {commit_sha}"
                )
        if narrate is not None:
            narrate(f"Pulling {source.stable_id} from {checkout} at {commit_sha}...")
        try:
            result = ingest_course_repository(
                source=source,
                commit_sha=commit_sha,
                checkout_root=checkout,
            )
        except CourseRepositoryIngestError as error:
            failures.append(source.stable_id)
            if warn is not None:
                warn(f"  REFUSED [{source.stable_id}]: {error}")
            continue
        results.append(result)
        if narrate is not None:
            narrate(
                f"  {result.file_count} files, "
                + ", ".join(f"{key}={value}" for key, value in sorted(result.counts.items()))
                + (" (replayed)" if result.replayed else "")
            )

    report = {"sources": [result.summary() for result in results]}
    if failures:
        raise SyncCourseRepositoriesError(
            f"course repository pull refused: {', '.join(failures)}", report=report
        )
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_target_arguments(parser)
    parser.add_argument(
        "--from-disk",
        dest="from_disk",
        default=None,
        type=Path,
        help=(
            "Directory holding one checkout per registered source, named after the "
            "source stable id or the repository name."
        ),
    )
    parser.add_argument(
        "--checkout",
        action="append",
        default=[],
        metavar="STABLE_ID=PATH",
        help="Explicit checkout for one source. Repeatable.",
    )
    parser.add_argument(
        "--stable-id",
        action="append",
        default=[],
        dest="stable_ids",
        help="Limit the run to these registered sources. Repeatable.",
    )
    parser.add_argument(
        "--checkout-plan",
        action="store_true",
        help=(
            "Print the registered sources and the checkout each would be read from, "
            "then exit without reading or writing anything."
        ),
    )
    parser.add_argument(
        "--allow-modified-checkout",
        action="store_true",
        help=(
            "Proceed when a checkout has uncommitted changes or is not on the "
            "registered branch. HEAD is what gets imported either way -- the "
            "snapshot is `git archive HEAD`, exactly what the push route "
            "downloads -- so uncommitted edits are not included. Commit them "
            "to see them."
        ),
    )
    parser.add_argument(
        "--require-public-commit",
        action="store_true",
        help=(
            "Refuse a checkout whose HEAD is not on a branch of the public GitHub "
            "repository. Imported pages link back to the commit they came from, so "
            "a commit only this machine has publishes source links, images and edit "
            "affordances that can only 404. Reachability is read from the checkout's "
            "own remote-tracking branches, so this stays offline."
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Print only the machine-readable JSON summary; suppress progress lines.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    configure_target(parser, args)

    root = args.from_disk.expanduser() if args.from_disk is not None else None
    try:
        explicit = parse_explicit_checkouts(args.checkout)
        sources = select_sources(args.stable_ids, explicit=explicit, root=root)
    except SyncCourseRepositoriesError as error:
        print(json.dumps({"error": str(error)}, indent=2))
        return 1

    if args.checkout_plan:
        for stable_id, repository, branch, target in checkout_plan(
            sources, root=root, explicit=explicit
        ):
            print(f"{stable_id}\t{repository}\t{branch}\t{target}")
        return 0

    from courses.services.local_course_seed import LocalCourseSeedError, assert_local_database

    try:
        assert_local_database()
    except LocalCourseSeedError as error:
        print(
            json.dumps(
                {
                    "error": (
                        "pulling content writes course rows and is refused outside a "
                        f"local or test SQLite database ({error})."
                    )
                },
                indent=2,
            )
        )
        return 1

    try:
        report = pull(
            sources=sources,
            checkouts=explicit,
            root=root,
            allow_modified_checkout=args.allow_modified_checkout,
            require_public_commit=args.require_public_commit,
            narrate=(lambda message: print(message)) if not args.quiet else None,
            warn=lambda message: print(message, file=sys.stderr),
        )
    except SyncCourseRepositoriesError as error:
        if error.report is not None:
            print(json.dumps(error.report, indent=2, sort_keys=True))
        print(json.dumps({"error": str(error)}, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
