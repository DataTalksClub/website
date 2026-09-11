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
never a second ``CustomUser`` row.  See ``scripts.prod.registrant_import`` for the
full matching contract and ``events.models.EventRegistrantIdentity`` /
``EventRegistration`` for the two tables this writes.

This requires event identities to already exist (``scripts/prod/import_events.py``,
section 5.2 of the ingest inventory) -- it never mints one itself.  An event
this script cannot resolve a source identity for is reported under
``awaiting_identity_events`` and skipped, not created.

**Two providers, as of 2026-09-11.**  Luma (``--luma-source``, always run) and
now Eventbrite (``--eventbrite-source`` + ``--eventbrite-identities``, opt-in
-- omit both to run Luma alone, exactly as before).  The report nests each
provider's own numbers under its own key (``"luma"``, ``"eventbrite"``) rather
than merging them, the same convention ``scripts/prod/import_events.py`` uses
throughout.  See ``scripts/prod/registration_sources/eventbrite_registrants.py``
for why Eventbrite's identity resolution is not the same lookup Luma's is.

Resumable at event granularity: one event's registrant rows are read and
written inside a single transaction, and only marked complete once that
transaction commits.  A re-run skips a completed event without reopening its
file -- see ``events.models.EventRegistrantImportProgress``.

That skip is what makes a resume safe, and it is also why a plain re-run picks
up nothing from a *newer* export.  Luma is not frozen: people keep registering
for events we already hold, and a refreshed export drops the ones who cancelled.
Eventbrite, unlike Luma, really is frozen history -- there is no newer
Eventbrite export coming -- but ``--refresh`` still applies to it uniformly,
since nothing about the mechanics differs.  ``--refresh`` re-reads every event
and replaces each one's registration facts wholesale.  See
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

    uv run --frozen python scripts/prod/import_event_registrants.py \\
        --database .tmp/local.sqlite3 \\
        --luma-source .local/migration-data/events/luma-aggregate-v1 \\
        --eventbrite-source .local/migration-data/events/eventbrite/aggregate-v1.zip \\
        --eventbrite-identities ~/prod/dtc-data/eventbrite-event-identities.json \\
        --dry-run
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
        "--eventbrite-source",
        type=Path,
        default=None,
        help=(
            "A prepared Eventbrite export archive -- the same flattened "
            "aggregate-v1.zip scripts/prod/import_events.py reads for "
            "registration counts (.local/migration-data/events/eventbrite/"
            "aggregate-v1.zip by default there). Omit to skip the Eventbrite "
            "leg entirely; when given, --eventbrite-identities is required."
        ),
    )
    parser.add_argument(
        "--eventbrite-identities",
        type=Path,
        default=None,
        help=(
            "eventbrite-event-identities.json -- the reviewed mapping from "
            "numeric Eventbrite event id to canonical Event source identity "
            "(events.eventbrite_content uses the same file for descriptions). "
            "Required when --eventbrite-source is given."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Validate every selected event through the real row readers and"
            " resolution gates, and report the counts (for --refresh, the"
            " predicted registration diff) that apply would carry out."
            " Writes nothing; malformed input exits nonzero."
        ),
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


def _run_provider(
    *, provider: str, pending: object, dry_run: bool, refresh: bool
) -> dict[str, object]:
    from scripts.prod.registrant_import import import_registrants, plan_registrants

    if dry_run:
        # The real plan pass: every selected event's rows go through the
        # same reader and the same resolution gates apply uses, so a bad
        # header, a mismatched event id, an oversized file, or an
        # unresolved target is refused here with the identical refusal --
        # and a valid refresh plan predicts the aggregate diff -- all
        # without a single write (audit REL-11).
        return {
            **plan_registrants(provider=provider, pending=pending, refresh=refresh).as_dict(),
            "refresh": refresh,
            "applied": False,
        }
    result = import_registrants(provider=provider, pending=pending, refresh=refresh)
    return {**result.as_dict(), "refresh": refresh, "applied": True}


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if bool(args.eventbrite_source) != bool(args.eventbrite_identities):
        parser.error("--eventbrite-source and --eventbrite-identities must be given together")
    configure_target(parser, args)

    from scripts.prod.registrant_import import RegistrantImportError

    # The events app owns no provider file format, so the reader that knows what
    # a Luma/Eventbrite export looks like is supplied here, by the ingestion layer.
    from scripts.prod.registration_sources.luma_registrants import (
        PROVIDER as LUMA_PROVIDER,
        luma_registrant_sources,
    )

    report: dict[str, object] = {}
    try:
        luma_pending = luma_registrant_sources(args.luma_source.resolve())
        report["luma"] = _run_provider(
            provider=LUMA_PROVIDER, pending=luma_pending, dry_run=args.dry_run, refresh=args.refresh
        )
        if args.eventbrite_source is not None:
            from scripts.prod.registration_sources.eventbrite_registrants import (
                PROVIDER as EVENTBRITE_PROVIDER,
                eventbrite_registrant_sources,
            )

            eventbrite_pending = eventbrite_registrant_sources(
                archive_path=args.eventbrite_source.resolve(),
                identities_path=args.eventbrite_identities.expanduser().resolve(),
            )
            report["eventbrite"] = _run_provider(
                provider=EVENTBRITE_PROVIDER,
                pending=eventbrite_pending,
                dry_run=args.dry_run,
                refresh=args.refresh,
            )
    except RegistrantImportError as error:
        # The error carries a condition code, never a source value.
        print(json.dumps({"error": str(error)}, indent=2))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
