#!/usr/bin/env python3
"""Backfill historical course-cohort enrollments from Mailchimp's export tags.

One-time import (safe to replay -- see below). Reads the same Mailchimp
audience export's **subscribed** CSV that
``scripts/prod/import_mailchimp_event_tags.py`` and
``scripts/prod/import_mailchimp_subscriptions.py`` already read, read in
place, and writes ``courses.models.Enrollment`` rows for the tagged
course-cohort history -- never ``courses.models.CourseRegistration``, and
never ``courses.models.CourseInterest``; see
``courses.services.mailchimp_course_tag_import`` for the full reasoning
behind both of those exclusions.

This importer deliberately does **not** bootstrap: every tag it reads names
a cohort that must already exist (from ``import_legacy_zoomcamp`` and
``import_cmp_content``, run earlier). A tag whose cohort this database does
not hold is skipped and reported, never guessed at or created here -- running
this before the course catalogue exists is not an error, it is a report full
of ``tags_missing_cohort`` entries and zero enrollments written. See
``scripts/prod/__init__.py``'s module docstring for why that distinction
(bootstrapping vs. reconciling) matters and is checked.

Only rows carrying at least one of the reviewed course-cohort tags in
``courses.services.mailchimp_course_tag_import.TAG_COHORT_MAP`` are read for
anything beyond the membership check -- see that module's docstring for the
full tag table and its provenance
(``_docs/runbooks/ingest-script-inventory.md``, course tags section). A row
whose email does not match an existing account is skipped and counted:
this importer never creates a ``CustomUser``.

Replaying is safe: ``Enrollment`` carries a ``(student, course)`` unique
constraint, and writes go through ``get_or_create``, so a second run against
an unchanged export changes nothing.

    uv run --frozen python scripts/prod/import_mailchimp_course_tags.py \\
        --database .tmp/local.sqlite3 \\
        --export-dir ~/prod/dtc-data/mailchimp-export

    uv run --frozen python scripts/prod/import_mailchimp_course_tags.py \\
        --database .tmp/local.sqlite3 \\
        --export-dir ~/prod/dtc-data/mailchimp-export --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.prod.target import add_target_arguments, configure_target  # noqa: E402

SYNC_MODEL = "one-time"
# Reconciles tagged rows against cohorts other importers already wrote
# (import_legacy_zoomcamp, import_cmp_content) and accounts other importers
# already created; never creates a CustomUser, a Cohort, or a
# CourseRegistration. Never bootstraps an empty database -- see the module
# docstring.
BOOTSTRAPS_EMPTY_DATABASE = False

# The only file this script ever opens. Mailchimp's export also carries
# unsubscribed/cleaned CSVs alongside this one in the same directory -- they
# are never globbed for or read, same as its two siblings.
_SUBSCRIBED_PREFIX = "subscribed_email_audience_export_"


class MailchimpCourseTagImportFailure(RuntimeError):
    """A safe refusal that carries a condition code, never a source value."""


def _resolve_subscribed_file(export_dir: Path) -> Path:
    """Find the subscribed export CSV in ``export_dir`` by its fixed prefix.

    Mailchimp appends a per-export hash to the filename (e.g.
    ``subscribed_email_audience_export_ed9afb9406.csv``), so this globs by
    prefix rather than requiring an exact name -- same convention as its
    siblings.
    """

    matches = sorted(export_dir.glob(f"{_SUBSCRIBED_PREFIX}*.csv"))
    if len(matches) != 1:
        raise MailchimpCourseTagImportFailure("export-dir-ambiguous-subscribed")
    return matches[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_target_arguments(parser)
    parser.add_argument(
        "--export-dir",
        required=True,
        type=Path,
        help=(
            "Directory holding the Mailchimp export CSVs, read in place "
            "(never copy this into the repository worktree). Only the "
            "subscribed file in it is opened."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Compute cohort-match, account-match and enrollment counts "
            "against the real database, through read-only queries only. "
            "Write nothing."
        ),
    )
    args = parser.parse_args(argv)

    try:
        subscribed_file = _resolve_subscribed_file(args.export_dir.resolve())
    except MailchimpCourseTagImportFailure as error:
        print(json.dumps({"error": str(error)}, indent=2))
        return 1

    configure_target(parser, args)

    from courses.services.mailchimp_course_tag_import import (
        MailchimpCourseTagImportError,
        import_mailchimp_course_tags,
    )

    try:
        result = import_mailchimp_course_tags(subscribed=subscribed_file, apply=not args.dry_run)
    except MailchimpCourseTagImportError as error:
        print(json.dumps({"error": str(error)}, indent=2))
        return 1

    print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
