#!/usr/bin/env python3
"""Import attendee-level event registrants, consolidated against real accounts.

One-time import.  Reads the same prepared Luma export directory that
``scripts/prod/import_events.py`` derives registration *counts* from (see
``_docs/runbooks/ingest-script-inventory.md`` section 6), but this script
reads the attendee-level rows themselves -- something no other importer does,
by design: ``events.importers`` is aggregate-only and never lets an attendee
value cross its own module boundary.

Every registrant row is consolidated against ``accounts_customuser`` by
``normalized_email`` first, so a person who both took a course and registered
for an event resolves to one account, never two.  An unmatched row becomes a
new, login-incapable registrant-only identity in the same email-keyed space --
never a second ``CustomUser`` row.  See ``events.registrant_import`` for the
full matching contract and ``events.models.EventRegistrantIdentity`` /
``EventRegistration`` for the two tables this writes.

This requires event identities to already exist (``scripts/prod/import_events.py``,
section 5.2 of the ingest inventory) -- it never mints one itself.  An event
this script cannot resolve a source identity for is reported under
``awaiting_identity_events`` and skipped, not created.

Eventbrite is not read yet -- see the module docstring in
``events/registrant_import.py`` for why.

Resumable at event granularity: one event's registrant rows are read and
written inside a single transaction, and only marked complete once that
transaction commits.  A re-run skips a completed event without reopening its
file -- see ``events.models.EventRegistrantImportProgress``.

That skip is what makes a resume safe, and it is also why a plain re-run picks
up nothing from a *newer* export.  Luma is not frozen: people keep registering
for events we already hold, and a refreshed export drops the ones who cancelled.
``--refresh`` is the pass for that -- it re-reads every event and replaces each
one's registration facts wholesale.  See
``_docs/runbooks/event-registration-pull.md`` for when to run which.

    uv run --frozen python scripts/prod/import_event_registrants.py \\
        --database .tmp/production-prep-current.sqlite3 \\
        --luma-source /data/tmp/luma-eventbrite-export/luma-aggregate-v1

    uv run --frozen python scripts/prod/import_event_registrants.py \\
        --database .tmp/production-prep-current.sqlite3 \\
        --luma-source /data/tmp/luma-eventbrite-export/luma-aggregate-v1 --dry-run

    uv run --frozen python scripts/prod/import_event_registrants.py \\
        --database .tmp/production-prep-current.sqlite3 \\
        --luma-source <a newer prepared export> --refresh
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
# Requires events.Event rows (from import_events.py) and, for the interesting
# "matched to an existing account" path, accounts_customuser rows (from
# import_cmp_learners.py) to already be present. It reconciles; it does not
# bootstrap.
BOOTSTRAPS_EMPTY_DATABASE = False

LUMA_RELATIVE_SOURCE = Path(".local/migration-data/events/luma-aggregate-v1")


class EventRegistrantImportCliError(RuntimeError):
    """A safe refusal that carries a condition code, never a source value."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_target_arguments(parser)
    parser.add_argument(
        "--luma-source",
        type=Path,
        default=PROJECT_ROOT / LUMA_RELATIVE_SOURCE,
        help=(
            "A prepared Luma export directory (paired CSV + JSON checkpoint per "
            "event) -- the same shape scripts/prod/import_events.py reads for "
            "registration counts. The durable copy lives at "
            "/data/tmp/luma-eventbrite-export/luma-aggregate-v1."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what a run would find, using the same discovery pass. Writes nothing.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help=(
            "Re-read every event in the export, including ones already recorded "
            "complete, and replace each one's registration facts with what this "
            "export carries. Use this for a newer export of events we already "
            "have; without it a completed event is skipped, which is what makes "
            "an interrupted run safe to resume."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    configure_target(parser, args)

    from events.registrant_import import RegistrantImportError, import_registrants

    # The events app owns no provider file format, so the reader that knows what
    # a Luma export looks like is supplied here, by the ingestion layer.
    from scripts.prod.registration_sources.luma_registrants import (
        PROVIDER,
        luma_registrant_sources,
    )

    source = args.luma_source.resolve()
    try:
        pending = luma_registrant_sources(source)
        if args.dry_run:
            report: dict[str, object] = {
                "provider": PROVIDER,
                "events_total": len(pending),
                "refresh": args.refresh,
                "applied": False,
            }
        else:
            result = import_registrants(
                provider=PROVIDER, pending=pending, refresh=args.refresh
            )
            report = {**result.as_dict(), "refresh": args.refresh, "applied": True}
    except RegistrantImportError as error:
        # The error carries a condition code, never a source value.
        print(json.dumps({"error": str(error)}, indent=2))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
