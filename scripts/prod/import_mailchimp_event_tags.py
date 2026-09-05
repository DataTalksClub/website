#!/usr/bin/env python3
"""Import Mailchimp's event-category tags as registrant interest signals.

One-time import (safe to replay -- see below). Reads the same Mailchimp
audience export's **subscribed** CSV that
``scripts/prod/import_mailchimp_subscriptions.py`` reads, read in place, and
writes broad interest-category signals onto the same identity pool
``scripts/prod/import_event_registrants.py`` populates --
``events.models.EventRegistrantInterestSignal``, never
``events.models.EventRegistration`` (a Mailchimp tag never names a specific
event; see ``events.mailchimp_tag_import`` for the full reasoning). Matched
by ``normalized_email`` against accounts and prior registrant identities
other importers already created; a row matching neither creates a new,
login-incapable registrant-only identity, exactly like
``scripts/prod/import_event_registrants.py`` does for a real Luma/Eventbrite
row. Never creates a ``CustomUser`` account.

Only 8 of the export's 32 distinct ``TAGS`` values are read for anything --
the reviewed, hardcoded mapping in ``events.mailchimp_event_tag_categories``.
Course-cohort tags are completely out of scope (blocked on a separate,
unresolved decision gate) and three miscellaneous tags are dropped entirely
by owner decision -- see that module's docstring for the exact list and
reasoning. A row carrying none of the 8 event tags is skipped before any
identity lookup: nothing about it is read, stored, or referenced.

Replaying is safe: identity resolution and interest-signal writes are both
idempotent (a unique constraint backs the signal row; get_or_create backs the
identity lookup), so a second run against an unchanged export changes
nothing.

    uv run --frozen python scripts/prod/import_mailchimp_event_tags.py \\
        --database .tmp/local.sqlite3 \\
        --export-dir /data/tmp/mailchimp-export

    uv run --frozen python scripts/prod/import_mailchimp_event_tags.py \\
        --database .tmp/local.sqlite3 \\
        --export-dir /data/tmp/mailchimp-export --dry-run
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

from scripts.prod.target import add_target_arguments, configure_target  # noqa: E402

SYNC_MODEL = "one-time"
# Reconciles against accounts and registrant identities other importers
# already created; never creates a CustomUser, and only ever creates a
# registrant-only EventRegistrantIdentity the same way
# import_event_registrants.py does. Never bootstraps an empty database.
BOOTSTRAPS_EMPTY_DATABASE = False

# The only file this script ever opens. Mailchimp's export also carries
# unsubscribed/cleaned CSVs alongside this one in the same directory -- they
# are never globbed for or read, same as import_mailchimp_subscriptions.py.
_SUBSCRIBED_PREFIX = "subscribed_email_audience_export_"


class MailchimpEventTagImportFailure(RuntimeError):
    """A safe refusal that carries a condition code, never a source value."""


def _resolve_subscribed_file(export_dir: Path) -> Path:
    """Find the subscribed export CSV in ``export_dir`` by its fixed prefix.

    Mailchimp appends a per-export hash to the filename (e.g.
    ``subscribed_email_audience_export_ed9afb9406.csv``), so this globs by
    prefix rather than requiring an exact name -- same convention as
    ``import_mailchimp_subscriptions.py``.
    """

    matches = sorted(export_dir.glob(f"{_SUBSCRIBED_PREFIX}*.csv"))
    if len(matches) != 1:
        raise MailchimpEventTagImportFailure("export-dir-ambiguous-subscribed")
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
            "Compute match and signal counts against the real database, "
            "through read-only queries only. Write nothing."
        ),
    )
    args = parser.parse_args(argv)

    try:
        subscribed_file = _resolve_subscribed_file(args.export_dir.resolve())
    except MailchimpEventTagImportFailure as error:
        print(json.dumps({"error": str(error)}, indent=2))
        return 1

    configure_target(parser, args)

    from events.mailchimp_tag_import import (
        MailchimpEventTagImportError,
        import_mailchimp_event_tags,
    )

    try:
        result = import_mailchimp_event_tags(subscribed=subscribed_file, apply=not args.dry_run)
    except MailchimpEventTagImportError as error:
        print(json.dumps({"error": str(error)}, indent=2))
        return 1

    print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
