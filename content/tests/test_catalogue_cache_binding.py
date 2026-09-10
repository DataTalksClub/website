"""Regression tests for the catalogue cache's release binding (ARC-01).

Two defects this pins shut:

* a database failure during a catalogue read used to be swallowed *inside*
  the release-keyed cache and stored as an empty collection, so a recovered
  database kept serving emptiness until the next import changed the key; and
* the row query re-resolved the source's active pointer instead of reading
  the release the cache key names, so an activation racing the read could
  build -- and cache -- release B's content under a key that was read as A.

The catalogue itself is loaded into every test database (see
``test_catalogue.py``); these tests read that same published content.
"""

from __future__ import annotations

from unittest import mock

from django.db import OperationalError, connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from content import catalogue
from content.models import ContentDocument, ContentSource
from content.tests.factories import activate, make_ready_release


class CacheBindingTestBase(TestCase):
    def setUp(self) -> None:
        super().setUp()
        # The release cache is process-wide; start each test from cold so the
        # queries counted below are this test's own.
        catalogue._records.cache_clear()
        self.source = ContentSource.objects.get(stable_id=catalogue.PUBLIC_CONTENT_STABLE_ID)


class FailedReadRecoveryTests(CacheBindingTestBase):
    def test_a_failed_row_read_raises_and_the_next_read_recovers(self) -> None:
        real_filter = ContentDocument.objects.filter
        failures = iter([OperationalError("catalogue row read lost")] * 1)

        def flaky_filter(*args, **kwargs):
            try:
                raise next(failures)
            except StopIteration:
                return real_filter(*args, **kwargs)

        with mock.patch.object(ContentDocument.objects, "filter", flaky_filter):
            with self.assertRaises(OperationalError):
                catalogue.records("book")

        # Nothing failed entered the cache: the recovering read runs the row
        # query again (pointer lookup plus rows) and answers with the published
        # books, not the empty collection a swallowed failure used to leave.
        with CaptureQueriesContext(connection) as recovery:
            books = catalogue.books()

        self.assertGreaterEqual(len(recovery), 2)
        self.assertTrue(books)


class ActivationRaceTests(CacheBindingTestBase):
    def test_a_key_answers_for_its_own_release_never_the_current_pointer(self) -> None:
        before = catalogue.records("fixture")
        self.assertEqual(before, ())
        # The key a request reads *before* the activation, captured while the
        # seeded release is still the active one, in the form the module's own
        # ``active_release_id()`` hands over: text.
        self.source.refresh_from_db()
        key_read_before_activation = str(self.source.active_release_id or "")

        release = make_ready_release(self.source, commit_character="b")
        activate(self.source, release)

        # The published release now carries one fixture record. A read whose
        # key was resolved *before* that activation -- the race window -- must
        # finish on the release it read, not follow the pointer mid-request.
        with mock.patch.object(catalogue, "active_release_id", lambda: key_read_before_activation):
            raced = catalogue.records("fixture")

        self.assertEqual(raced, before)
        # And a read after the activation sees the new release's content.
        self.assertNotEqual(catalogue.records("fixture"), before)


class AbsentPointerTests(CacheBindingTestBase):
    def test_an_absent_pointer_answers_empty_without_reading_documents(self) -> None:
        ContentSource.objects.filter(stable_id=catalogue.PUBLIC_CONTENT_STABLE_ID).update(
            enabled=False
        )

        with CaptureQueriesContext(connection) as read:
            books = catalogue.books()

        # The disabled source's pointer lookup is the only query: an empty key
        # is an empty catalogue, not a document read (and not a failure).
        self.assertEqual(len(read), 1)
        self.assertEqual(books, ())
