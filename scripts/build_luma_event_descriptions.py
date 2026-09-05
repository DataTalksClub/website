#!/usr/bin/env python3
"""Process a Luma description export into staging, for events we already have.

Source -> staging.  The Luma export is the source; the artifact this writes,
``temporary/content/luma_event_descriptions.json``, is staging;
``scripts/prod/import_events.py`` moves it into the production database.  See
``_docs/runbooks/data-ingest.md`` for what those three words mean here.

Why this exists alongside the 421-record legacy corpus: that corpus is a frozen
one-time export whose descriptions come from the event description bridge, and
the bridge matches entries on the legacy ``_data/events.yaml`` tuple.  An event
discovered in a Luma export has no such tuple, so the bridge cannot carry its
description and a rebuild would blank it.  New events therefore get their own
staging artifact, and nothing pinned to the legacy corpus moves when it grows.

What a run does, per description file:

1. resolves the event by slug against the identities already in the database --
   it never creates one, so run ``import_events.py --discover-new-events-only``
   first for a genuinely new event;
2. skips an event that already has a reviewed description, because the legacy
   corpus wins over a re-render;
3. renders the Markdown through the bridge's own Markdown and link policies;
4. removes the "about the speaker" block and the platform footer with the same
   ``normalize_description_html`` that cleaned the legacy corpus.

**Reporting is the default and it writes nothing.**  A link nobody has reviewed
stops that event and is reported by URL, because approving a destination is a
person's decision: it is an edit to
``scripts/projection_build/event_description_link_policy.py``, never something
inferred here.  Re-run with ``--write`` once the report is clean.

    uv run --frozen python scripts/build_luma_event_descriptions.py \\
        --database .tmp/local.sqlite3
    uv run --frozen python scripts/build_luma_event_descriptions.py \\
        --database .tmp/local.sqlite3 --write
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.staging.luma_event_descriptions import (  # noqa: E402
    ARTIFACT_PATH,
    DEFAULT_DESCRIPTION_ROOT,
    LumaDescriptionError,
    build_artifact,
    build_record,
    discover_luma_descriptions,
    write_artifact,
)


def _configure(database: Path) -> None:
    os.environ["DTC_ENVIRONMENT"] = "local"
    os.environ["DTC_SQLITE_PATH"] = str(database)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "website.settings.local")

    import django

    django.setup()


def run(*, descriptions: Path, apply: bool = False) -> dict[str, Any]:
    from events.models import Event, EventContent

    sources = discover_luma_descriptions(descriptions)
    identities = {
        event.slug: event for event in Event.objects.filter(slug__in=[s.slug for s in sources])
    }
    described = set(
        EventContent.objects.exclude(description_html="").values_list("event__slug", flat=True)
    )

    records: list[dict[str, Any]] = []
    unmatched: list[str] = []
    already_described: list[str] = []
    needs_link_review: list[dict[str, str]] = []

    # One renderer for the whole run: building it reads the route registry the
    # link policy resolves internal paths against.
    from scripts.staging.luma_event_descriptions import _renderer

    renderer = _renderer()
    for source in sources:
        event = identities.get(source.slug)
        if event is None:
            unmatched.append(source.slug)
            continue
        if source.slug in described:
            already_described.append(source.slug)
            continue
        try:
            records.append(build_record(source, identity_id=str(event.id), renderer=renderer))
        except LumaDescriptionError as error:
            reason = str(error)
            if not reason.startswith("luma_description_render_refused"):
                raise
            needs_link_review.append({"slug": source.slug, "reason": reason})

    artifact = build_artifact(records)
    report: dict[str, Any] = {
        "descriptions_read": len(sources),
        "resolved_to_an_event": len(sources) - len(unmatched),
        "already_described": len(already_described),
        "prepared": len(records),
        "needs_link_review": needs_link_review,
        # A slug with no identity is not an error: the event has not been
        # discovered yet, or Luma renamed it. Named so it cannot be missed.
        "no_identity_yet": sorted(unmatched),
        "counts": artifact["counts"],
        "applied": apply,
    }
    if apply:
        report["written_to"] = str(write_artifact(artifact).relative_to(REPOSITORY_ROOT))
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--descriptions", type=Path, default=DEFAULT_DESCRIPTION_ROOT)
    parser.add_argument("--artifact", type=Path, default=ARTIFACT_PATH)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Replace the staging artifact. Without it the run reports and writes nothing.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        _configure(args.database.resolve())
        report = run(descriptions=args.descriptions.resolve(), apply=args.write)
    except LumaDescriptionError as error:
        print(json.dumps({"error": str(error)}, indent=2))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if not report["needs_link_review"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
