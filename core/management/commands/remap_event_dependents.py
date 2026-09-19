"""Re-point the satellite tables' event keys after the events rebuild.

The D4.1 cutover (P5) drops the legacy ``events_*`` tables and rebuilds them
from the package migrations, which assigns fresh integer primary keys.  The
satellite apps -- event Q&A, historical registrations, attendee registrants --
kept their rows, but their ``event_id`` columns still hold the pre-rebuild
values (the site identity UUID the shared row now carries as ``content_id``).
This command re-reads every stale key through ``content_id`` and writes the new
row key, then reports the row counts the D4.1 verification asks for.

Dry-run is the default and only reports.  ``--apply`` performs the rewrite.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandParser
from django.db import connection

#: (table, key column) pairs holding a stale event reference.  Every row in
#: these tables points at an event, so a stale key is one that no longer
#: matches ``events_event.id``.
_SATELLITE_KEYS = (
    ("event_qna_eventqnasession", "event_id"),
    ("event_registrants_eventregistrantidentity", "event_id"),
)


class Command(BaseCommand):
    help = (
        "Re-point satellite event keys through content_id after the events "
        "rebuild (P5, one-off for the D4.1 cutover)"
    )

    requires_system_checks: list[str] = []

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Rewrite the stale keys; without this the command only reports.",
        )

    def handle(self, *args: object, **options: object) -> None:
        del args
        apply_changes = bool(options["apply"])
        quoted = connection.ops.quote_name
        mode = "applying" if apply_changes else "dry run"
        total_stale = 0

        with connection.cursor() as cursor:
            existing = set(connection.introspection.table_names(cursor))
            for table, column in _SATELLITE_KEYS:
                if table not in existing:
                    continue
                stale = cursor.execute(
                    f"SELECT COUNT(*) FROM {quoted(table)} WHERE "  # noqa: S608 -- identifiers are code-owned literals.
                    f"{quoted(column)} IS NOT NULL AND {quoted(column)} NOT IN "
                    "(SELECT id FROM events_event)"
                ).fetchone()[0]
                self.stdout.write(f"{mode}: {table}: {stale} stale keys")
                total_stale += stale
                if apply_changes and stale:
                    cursor.execute(
                        f"UPDATE {quoted(table)} SET {quoted(column)} = ("  # noqa: S608 -- identifiers are code-owned literals.
                        "SELECT e.id FROM events_event e WHERE "
                        f"e.content_id = {quoted(table)}.{quoted(column)})"
                    )

        if apply_changes:
            self.stdout.write(self.style.SUCCESS(f"Re-pointed {total_stale} rows."))
        else:
            self.stdout.write("Dry run only; pass --apply to re-point.")
