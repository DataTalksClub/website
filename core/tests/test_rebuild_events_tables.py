"""The P5 rebuild command drops legacy ``events_`` tables and clears the label.

The suite cannot rehearse a real cutover, so the apply test drives the command
against the worker database's own ``events_*`` tables (empty here, and rolled
back by the test transaction afterwards) plus a probe table that names like the
legacy set but has no dependents.  The contract under test: every ``events_``
table goes, the migration/content-type/permission rows for the label go, and
nothing else does.
"""

from __future__ import annotations

from io import StringIO

from django.core.management import call_command
from django.db import connection
from django.test import TestCase


def _table_names() -> set[str]:
    with connection.cursor() as cursor:
        return set(connection.introspection.table_names(cursor))


def _scalar(sql: str) -> int:
    with connection.cursor() as cursor:
        return int(cursor.execute(sql).fetchone()[0])


def _call(*args: str) -> str:
    out = StringIO()
    call_command("rebuild_events_tables", *args, stdout=out)
    return out.getvalue()


class RebuildEventsTablesTests(TestCase):
    def test_dry_run_reports_but_keeps_everything(self) -> None:
        with connection.cursor() as cursor:
            cursor.execute("CREATE TABLE events_rebuild_probe (id integer PRIMARY KEY)")
        self.assertIn("events_rebuild_probe", _table_names())

        output = _call()

        self.assertIn("dry run", output)
        self.assertIn("events_rebuild_probe", output)
        self.assertIn("Dry run only", output)
        self.assertIn("events_rebuild_probe", _table_names())
        self.assertGreater(
            _scalar("SELECT COUNT(*) FROM django_migrations WHERE app = 'events'"), 0
        )

    def test_apply_drops_events_tables_and_clears_the_registry(self) -> None:
        with connection.cursor() as cursor:
            cursor.execute("CREATE TABLE events_rebuild_probe (id integer PRIMARY KEY)")

        output = _call("--apply")

        self.assertIn("Dropped", output)
        tables = _table_names()
        self.assertFalse(
            [name for name in tables if name.startswith("events_")],
            "an events_ table survived the rebuild",
        )
        self.assertIn("django_site", tables)
        self.assertEqual(_scalar("SELECT COUNT(*) FROM django_migrations WHERE app = 'events'"), 0)
        self.assertEqual(
            _scalar("SELECT COUNT(*) FROM django_content_type WHERE app_label = 'events'"), 0
        )
