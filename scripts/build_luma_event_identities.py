#!/usr/bin/env python3
"""Match Luma export event IDs to the canonical ``Event`` each one really is.

The Eventbrite sibling of this file, ``~/prod/dtc-data/eventbrite-event-identities.json``,
resolves Eventbrite's numeric ids via ``events.xlsx`` (an identity table Eventbrite's
export itself carries). Luma's export carries no such table -- the only per-event
facts it has are ``title``/``start_at`` (read by
:func:`scripts.prod.registration_sources.luma.discover_luma_events`, the same
reader ``scripts/prod/import_events.py``'s ``activate_unambiguous_mappings`` step
already uses every run).

This script does not reimplement that matching rule. It reuses
:class:`scripts.prod.registrant_import.ExistingEventIndex` verbatim -- the exact
same case/whitespace-normalized-title-plus-calendar-date equality, ambiguous
left ambiguous, nothing fuzzy or ranked -- against whatever canonical ``Event``
rows the target database already holds (ordinarily the 421-event reviewed
manifest, already imported).

What this produces is an *identity* mapping only -- which canonical Event a
Luma export id corresponds to. It intentionally has nothing to say about
whether that event's registration count is activated for public display:
``events.services.resolve_unmatched_aggregates`` is explicit that resolving an
aggregate to an ``Event`` and activating that count for public display are
"a separate, still-gated concern" (see its docstring, and
``_docs/migration-data/event-registration-sources.json``'s
``activation_state: "mapping_review_required"``). Nothing here writes to that
gate.

**Reporting is the default and it writes nothing**, the same convention every
other staging builder in this repository uses. Re-run with ``--write`` once
the report looks right.

    uv run --frozen python scripts/build_luma_event_identities.py \\
        --database .tmp/local.sqlite3 \\
        --luma-source ~/prod/dtc-data/local-migration-data/events/luma-aggregate-v1

    uv run --frozen python scripts/build_luma_event_identities.py \\
        --database .tmp/local.sqlite3 \\
        --luma-source ~/prod/dtc-data/local-migration-data/events/luma-aggregate-v1 \\
        --output ~/prod/dtc-data/luma-event-identities.json --write
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

DEFAULT_OUTPUT = Path("~/prod/dtc-data/luma-event-identities.json").expanduser()


def _configure(database: Path) -> None:
    os.environ["DTC_ENVIRONMENT"] = "local"
    os.environ["DTC_SQLITE_PATH"] = str(database)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "website.settings.local")

    import django

    django.setup()


def build(*, luma_source: Path) -> dict[str, Any]:
    from events.models import Event, canonical_event_date, normalize_event_title
    from scripts.prod.registrant_import import (
        EXISTING_EVENT_AMBIGUOUS,
        EXISTING_EVENT_DATE_UNUSABLE,
        EXISTING_EVENT_MATCHED,
        EXISTING_EVENT_NONE,
        ExistingEventIndex,
    )
    from scripts.prod.registration_sources.luma import discover_luma_events

    discovered = discover_luma_events(luma_source)
    index = ExistingEventIndex()

    events: list[dict[str, Any]] = []
    reason_totals: Counter[str] = Counter()

    for item in discovered:
        if not item.title:
            events.append(
                {
                    "luma_event_id": item.external_event_identifier,
                    "status": "unresolved",
                    "reason": "empty_title",
                    "title": item.title,
                    "start_at": item.start_at,
                }
            )
            reason_totals["empty_title"] += 1
            continue

        match = index.match(title=item.title, start_at=item.start_at)
        if match.outcome == EXISTING_EVENT_MATCHED:
            event = match.event
            assert event is not None
            events.append(
                {
                    "luma_event_id": item.external_event_identifier,
                    "status": "resolved",
                    "title": item.title,
                    "normalized_title": normalize_event_title(item.title),
                    "date": match.date,
                    "canonical_slug": event.slug,
                    "canonical_public_id": event.public_id,
                    "canonical_repository": event.source_repository,
                    "canonical_revision": event.source_revision,
                    "canonical_source_key": event.source_key,
                }
            )
            reason_totals["resolved"] += 1
        elif match.outcome == EXISTING_EVENT_AMBIGUOUS:
            events.append(
                {
                    "luma_event_id": item.external_event_identifier,
                    "status": "ambiguous",
                    "reason": "multiple_events_share_date_and_title",
                    "title": item.title,
                    "normalized_title": normalize_event_title(item.title),
                    "date": match.date,
                    "candidate_total": match.candidate_total,
                }
            )
            reason_totals["ambiguous"] += 1
        elif match.outcome == EXISTING_EVENT_DATE_UNUSABLE:
            events.append(
                {
                    "luma_event_id": item.external_event_identifier,
                    "status": "unresolved",
                    "reason": "provider_event_date_unusable",
                    "title": item.title,
                    "start_at": item.start_at,
                }
            )
            reason_totals["provider_event_date_unusable"] += 1
        else:
            assert match.outcome == EXISTING_EVENT_NONE
            events.append(
                {
                    "luma_event_id": item.external_event_identifier,
                    "status": "unresolved",
                    "reason": "no_existing_event",
                    "title": item.title,
                    "normalized_title": normalize_event_title(item.title),
                    "date": match.date,
                    "other_dates_with_this_title": list(match.other_dates_with_this_title),
                }
            )
            reason_totals["no_existing_event"] += 1

    canonical_events_with_date = sum(
        1 for event in Event.objects.all() if canonical_event_date(event.source_key) is not None
    )

    artifact = {
        "schema_version": 1,
        "description": (
            "Luma export event IDs (as found in "
            "~/prod/dtc-data/local-migration-data/events/luma-aggregate-v1/, one "
            "CSV+JSON pair per id) matched to a real DataTalks.Club canonical Event "
            "by exact case/whitespace-normalized title plus calendar date -- the "
            "same rule scripts/prod/import_events.py's activate_unambiguous_mappings "
            "already applies to aggregate resolution, reused verbatim here via "
            "scripts.prod.registrant_import.ExistingEventIndex, not reimplemented. "
            "No fuzzy or ranked matching. This is an identity mapping only; it does "
            "not activate any registration count for public display -- see "
            "events.services.resolve_unmatched_aggregates's docstring and "
            "_docs/migration-data/event-registration-sources.json's "
            "activation_state for why that stays a separate, human-reviewed step."
        ),
        "generated_at": datetime.now(UTC).isoformat(),
        "source": {
            "luma_source_root": str(luma_source),
            "matched_against_canonical_events_with_date": canonical_events_with_date,
        },
        "counts": {
            "luma_events_total": len(discovered),
            **reason_totals,
        },
        "events": events,
    }
    return artifact


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument(
        "--luma-source",
        required=True,
        type=Path,
        help="e.g. ~/prod/dtc-data/local-migration-data/events/luma-aggregate-v1",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Actually write --output. Without this flag, only report.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _configure(args.database.expanduser())

    artifact = build(luma_source=args.luma_source.expanduser())

    print(json.dumps(artifact["counts"], indent=2))
    print(f"output: {args.output.expanduser()} (write={args.write})")

    if args.write:
        output_path = args.output.expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(artifact, indent=2, sort_keys=False) + "\n", encoding="utf-8"
        )
        print(f"wrote {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
