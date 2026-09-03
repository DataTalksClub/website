#!/usr/bin/env python3
"""Register the pinned course-repository content sources.

Git-synchronized.  Which repositories exist is a database question -- the
enabled ``ContentSource`` rows with the course-repository adapter type -- and
both routes into the curriculum tables (the signed push webhook and
``scripts/prod/sync_course_repositories.py``) read exactly those rows.  A fresh
database has none, so this is how it gets its first ones.  See
``scripts/prod/__init__.py`` for what the two sync models mean; this one is
git-synchronized because the pinned input
(``content_sync/course_repository_sources.json``) is checked in and grows as
new course repositories are added.

Idempotent.  A source that already exists is reported and left untouched --
an operator who repointed a registered source's repository identity meant it,
and a re-run must not undo that.

    uv run --frozen python scripts/prod/sync_course_repository_sources.py \\
        --database .tmp/local.sqlite3
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SYNC_MODEL = "git-synchronized"
# It creates every registered ContentSource row from the pinned input, so a
# database with no rows of its own gets its first ones here.
BOOTSTRAPS_EMPTY_DATABASE = True


def _configure(database: Path) -> None:
    os.environ["DTC_ENVIRONMENT"] = "local"
    os.environ["DTC_SQLITE_PATH"] = str(database)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "website.settings.local")

    import django

    django.setup()


def sync(*, registration_input: Path | None = None) -> dict[str, object]:
    """Register every pinned source that is missing; leave every other one alone."""

    from content_sync.course_repository_registration import (
        CourseRepositoryRegistrationError,
        load_registration_input,
        seed_course_repository_sources,
    )

    try:
        registrations = load_registration_input(registration_input)
        report = seed_course_repository_sources(registrations)
    except CourseRepositoryRegistrationError as error:
        raise SyncCourseRepositorySourcesError(str(error)) from error
    return {"sources": report}


class SyncCourseRepositorySourcesError(RuntimeError):
    """A bounded refusal to register the pinned course-repository sources."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help=("Registration input JSON. Defaults to content_sync/course_repository_sources.json."),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _configure(args.database.resolve())

    try:
        report = sync(registration_input=args.input.resolve() if args.input is not None else None)
    except SyncCourseRepositorySourcesError as error:
        print(json.dumps({"error": str(error)}, indent=2))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
