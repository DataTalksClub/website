"""Warn on every ``migrate``, test run and ``check --database`` while any event is incomplete.

An event must have a type, a speaker, a description and a picture
(:mod:`events.publication`).  Tagged ``database``: it runs wherever Django checks
against the data -- ``migrate`` and the test runner do.  A warning, not an
error, because ``migrate`` aborts on an error and production still holds
incomplete events awaiting a backfill; ``manage.py check --database default
--fail-level WARNING`` turns it into a failing exit status.
"""

from __future__ import annotations

from typing import Any

from django.core.checks import Tags, Warning, register
from django.db import DatabaseError


@register(Tags.database)
def check_every_event_is_complete(app_configs: Any = None, **kwargs: Any) -> list[Warning]:
    del app_configs
    if not kwargs.get("databases"):
        return []
    from events.publication import completeness_summary

    try:
        summary = completeness_summary(limit=20)
    except DatabaseError:
        return []  # no schema yet; the migration checks report that
    if not summary["incomplete"]:
        return []
    missing = ", ".join(f"{part}: {count}" for part, count in summary["missing"].items() if count)
    public_ids = ", ".join(str(row["public_id"] or f"id {row['id']}") for row in summary["events"])
    return [
        Warning(
            f"{summary['incomplete']} event(s) lack a type, a speaker, a description or a "
            f"picture ({missing}).",
            hint=f"First public ids: {public_ids}. Fix them through the reviewed ingest or "
            "Studio; see events/publication.py.",
            id="dtc_events.W001",
        )
    ]
