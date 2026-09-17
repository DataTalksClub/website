"""The remap command re-points satellite keys through ``content_id``.

Like the rebuild rehearsal, the suite cannot rehearse a real cutover against a
populated legacy database, so the contract under test is the reporting shape:
the command enumerates the satellite tables that exist, reports their stale-key
counts, and never writes without ``--apply``.
"""

from __future__ import annotations

from io import StringIO

from django.core.management import call_command
from django.db import connection
from django.test import TestCase


def _table_names() -> set[str]:
    with connection.cursor() as cursor:
        return set(connection.introspection.table_names(cursor))


def _call(*args: str) -> str:
    out = StringIO()
    call_command("remap_event_dependents", *args, stdout=out)
    return out.getvalue()


class RemapEventDependentsTests(TestCase):
    def test_dry_run_reports_and_writes_nothing(self) -> None:
        tables = _table_names()
        qna_present = "event_qna_eventqnasession" in tables

        output = _call()

        self.assertIn("dry run", output)
        if qna_present:
            self.assertIn("event_qna_eventqnasession", output)
        self.assertNotIn("Re-pointed", output)

    def test_apply_without_stale_keys_reports_zero_writes(self) -> None:
        output = _call("--apply")

        # The test database's satellites already point at live package rows,
        # so a clean rehearsal re-points nothing and says so.
        self.assertIn("Re-pointed 0 rows.", output)
