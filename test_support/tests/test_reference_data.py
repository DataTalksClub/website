"""Reviewed reference content has to reach the database each worker connects to.

``django.test.utils.setup_databases`` clones the test database once per
``--parallel`` worker, and takes the ``serialized_rollback`` snapshot, as soon as
``create_test_db`` returns.  Anything loaded after that point lives only in the
database no test ever runs against, and every test that reads reviewed content
fails with an empty page instead of a missing fixture.
"""

from __future__ import annotations

from unittest.mock import patch

from django.db.backends.sqlite3.creation import DatabaseCreation
from django.test import SimpleTestCase, TestCase

from content.models import ContentDocument
from courses.models import Testimonial
from events.models import Event, EventAlias
from test_support.django_runner import IsolatedSQLiteCreation


class ReferenceDataLoadOrderTests(SimpleTestCase):
    def test_the_database_is_filled_before_create_test_db_returns(self) -> None:
        order: list[str] = []
        # A connection is never used here, only held: ``BaseDatabaseCreation``
        # deletes the attribute in ``__del__`` and complains if it was absent.
        creation = IsolatedSQLiteCreation(connection=None)

        def create(*args: object, **kwargs: object) -> str:
            order.append("create")
            return "test-database"

        def load() -> dict[str, int]:
            order.append("load")
            return {}

        with (
            patch.object(DatabaseCreation, "create_test_db", side_effect=create),
            patch("test_support.django_runner.load_reviewed_reference_data", side_effect=load),
        ):
            name = creation.create_test_db(verbosity=0)

        self.assertEqual(name, "test-database")
        self.assertEqual(order, ["create", "load"])


class ReferenceDataReachesThisWorkerTests(TestCase):
    def test_the_reviewed_rows_are_present_wherever_this_test_runs(self) -> None:
        self.assertEqual(Event.objects.count(), 421)
        self.assertEqual(EventAlias.objects.count(), 1_684)
        self.assertEqual(Testimonial.objects.count(), 6)
        self.assertGreater(ContentDocument.objects.count(), 0)
