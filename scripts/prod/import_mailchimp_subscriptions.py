#!/usr/bin/env python3
"""Import Mailchimp newsletter subscription status onto existing accounts.

One-time import (safe to replay -- see below). Reads a Mailchimp audience
export's **subscribed** CSV only, read in place -- and writes one fact onto
each matching account: ``CustomUser.newsletter_subscribed``. Matched by
``normalized_email`` against accounts other importers already created; never
creates an account of its own. The export's separate unsubscribed/cleaned
files are never opened by this script -- scope was deliberately narrowed to
subscribed-only; see ``accounts.services.mailchimp_subscription_import`` for
the full matching contract and the privacy-minimization decision
(``OPTIN_IP``, ``CONFIRM_IP``, ``NOTES`` and ``TAGS`` are never read into the
database).

Replaying is safe: a matched account's field is set ``True``, never toggled,
so a second run changes nothing.

    uv run --frozen python scripts/prod/import_mailchimp_subscriptions.py \\
        --database .tmp/local.sqlite3 \\
        --export-dir /data/tmp/mailchimp-export

    uv run --frozen python scripts/prod/import_mailchimp_subscriptions.py \\
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
# Depends on accounts already written by an earlier importer (the legacy
# zoomcamp import and/or import_cmp_learners) -- it only ever updates a
# matching existing row, never creates one.
BOOTSTRAPS_EMPTY_DATABASE = False

# The only file this script ever opens. Mailchimp's export also carries
# unsubscribed/cleaned CSVs alongside this one in the same directory -- they
# are deliberately never globbed for or read; see the module docstring.
_SUBSCRIBED_PREFIX = "subscribed_email_audience_export_"


class MailchimpImportFailure(RuntimeError):
    """A safe refusal that carries a condition code, never a source value."""


def _resolve_subscribed_file(export_dir: Path) -> Path:
    """Find the subscribed export CSV in ``export_dir`` by its fixed prefix.

    Mailchimp appends a per-export hash to the filename (e.g.
    ``subscribed_email_audience_export_ed9afb9406.csv``), so this globs by
    prefix rather than requiring an exact name.
    """

    matches = sorted(export_dir.glob(f"{_SUBSCRIBED_PREFIX}*.csv"))
    if len(matches) != 1:
        raise MailchimpImportFailure("export-dir-ambiguous-subscribed")
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
        "--batch-size",
        type=int,
        default=None,
        help="Rows per resolved batch (default: the service's own default).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute match counts against the real database. Write nothing.",
    )
    args = parser.parse_args(argv)

    try:
        subscribed_file = _resolve_subscribed_file(args.export_dir.resolve())
    except MailchimpImportFailure as error:
        print(json.dumps({"error": str(error)}, indent=2))
        return 1

    configure_target(parser, args)

    from accounts.services.mailchimp_subscription_import import (
        DEFAULT_BATCH_SIZE,
        MailchimpImportError,
        import_mailchimp_subscriptions,
    )

    batch_size = args.batch_size or DEFAULT_BATCH_SIZE
    try:
        result = import_mailchimp_subscriptions(
            subscribed=subscribed_file,
            batch_size=batch_size,
            apply=not args.dry_run,
        )
    except MailchimpImportError as error:
        print(json.dumps({"error": str(error)}, indent=2))
        return 1

    print(json.dumps(result.summary(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
