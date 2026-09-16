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

from community_base.content_sync.models import ContentSource as EngineContentSource
from django.db import OperationalError, connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from content import catalogue
from content.models import ContentDocument, ContentSource, SyncedDocument
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
                # The course records are one of the kinds still read from the
                # staged release; the synced kinds carry the same guarantee in
                # the classes below.
                catalogue.records("course")

        # Nothing failed entered the cache: the recovering read runs the row
        # query again (pointer lookup plus rows) and answers with the published
        # records, not the empty collection a swallowed failure used to leave.
        with CaptureQueriesContext(connection) as recovery:
            courses = catalogue.courses()

        self.assertGreaterEqual(len(recovery), 2)
        self.assertTrue(courses)


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
            courses = catalogue.courses()

        # The disabled source's pointer lookup is the only query: an empty key
        # is an empty catalogue, not a document read (and not a failure).
        self.assertEqual(len(read), 1)
        self.assertEqual(courses, ())


class SyncedWikiBindingTests(CacheBindingTestBase):
    """The same two guarantees for the wiki's synced-row authority (#384).

    The wiki reads ``SyncedDocument`` rows behind a stamp cache key instead of
    release-keyed ones, so the race the release binding removes cannot happen
    there -- but a failed read must still raise rather than cache emptiness,
    and a warmed answer must still follow the rows it was read from.
    """

    def setUp(self) -> None:
        super().setUp()
        catalogue._synced_records.cache_clear()

    def test_a_failed_row_read_raises_and_the_next_read_recovers(self) -> None:
        stamp = catalogue.synced_stamp(catalogue.WIKI_SOURCE_SLUG)
        real_filter = SyncedDocument.objects.filter
        failures = iter([OperationalError("wiki row read lost")])

        def flaky_filter(*args, **kwargs):
            try:
                raise next(failures)
            except StopIteration:
                return real_filter(*args, **kwargs)

        with (
            mock.patch.object(catalogue, "synced_stamp", lambda slug: stamp),
            mock.patch.object(SyncedDocument.objects, "filter", flaky_filter),
        ):
            with self.assertRaises(OperationalError):
                catalogue.wiki_pages()

        # Nothing that failed entered the cache: the recovering read answers
        # with the published pages, not the empty collection a swallowed
        # failure used to leave.
        with mock.patch.object(catalogue, "synced_stamp", lambda slug: stamp):
            pages = catalogue.wiki_pages()

        self.assertTrue(pages)

    def test_a_sync_write_moves_the_stamp_and_the_next_read_sees_it(self) -> None:
        before = [page["slug"] for page in catalogue.wiki_pages()]
        row = SyncedDocument.objects.get(
            source__slug=catalogue.WIKI_SOURCE_SLUG,
            content_kind=catalogue.WIKI_PAGE_KIND,
            stable_key=before[0],
        )
        row.record = {**row.record, "title": "Renamed wiki page"}
        row.save()

        pages = catalogue.wiki_pages()

        self.assertIn("Renamed wiki page", [page["title"] for page in pages])

    def test_an_absent_sync_answers_empty_without_reading_rows(self) -> None:
        EngineContentSource.objects.filter(slug=catalogue.WIKI_SOURCE_SLUG).update(is_enabled=False)

        with CaptureQueriesContext(connection) as read:
            pages = catalogue.wiki_pages()

        # The stamp aggregate is the only query: an absent source is an empty
        # wiki, not a row read (and not a failure).
        self.assertEqual(len(read), 1)
        self.assertEqual(pages, ())


class SyncedSitePageBindingTests(CacheBindingTestBase):
    """The same guarantees for the site page rows (#384).

    The platform catalog and the ``/slack`` page are one row apiece on the
    editorial source, read behind the same stamp cache key as the wiki kinds,
    so a warmed answer must follow the row it was read from and an absent
    source must answer empty without reading rows.
    """

    def setUp(self) -> None:
        super().setUp()
        catalogue._synced_records.cache_clear()

    def test_a_sync_write_moves_the_stamp_and_the_next_read_sees_it(self) -> None:
        before = catalogue.podcast_platforms()
        row = SyncedDocument.objects.get(
            source__slug=catalogue.EDITORIAL_SOURCE_SLUG,
            content_kind="podcast_platforms",
        )
        row.record = {**row.record, "platforms": row.record["platforms"][:1]}
        row.save()

        platforms = catalogue.podcast_platforms()

        self.assertEqual([platform["provider"] for platform in platforms], ["apple"])
        self.assertGreaterEqual(len(before), 2)

    def test_an_absent_sync_answers_empty_without_reading_rows(self) -> None:
        EngineContentSource.objects.filter(slug=catalogue.EDITORIAL_SOURCE_SLUG).update(
            is_enabled=False
        )

        with CaptureQueriesContext(connection) as read:
            page = catalogue.slack_page()

        # The stamp aggregate is the only query: an absent source is no page,
        # not a row read (and not a failure).
        self.assertEqual(len(read), 1)
        self.assertIsNone(page)


class SyncedPeopleBindingTests(CacheBindingTestBase):
    """The same two guarantees for the synced people authority (#384).

    A profile's derived credits also follow the live event rows, so a failed
    event read is covered here too: the events are one of the authorities the
    profiles are derived from, and an outage must raise rather than cache
    people with no talks.
    """

    def setUp(self) -> None:
        super().setUp()
        catalogue._synced_records.cache_clear()
        catalogue._people.cache_clear()

    def test_a_sync_write_moves_the_stamp_and_the_next_read_sees_it(self) -> None:
        before = [person["slug"] for person in catalogue.people()]
        row = SyncedDocument.objects.get(
            source__slug=catalogue.PEOPLE_SOURCE_SLUG,
            content_kind=catalogue.PEOPLE_KIND,
            stable_key=before[0],
        )
        row.record = {**row.record, "title": "Renamed profile"}
        row.save()

        people = catalogue.people()

        self.assertIn("Renamed profile", [person["title"] for person in people])

    def test_a_failed_event_read_raises_and_the_next_read_recovers(self) -> None:
        with mock.patch.object(
            catalogue, "_published_event_records", side_effect=OperationalError("event read lost")
        ):
            with self.assertRaises(OperationalError):
                catalogue.people()

        # Nothing that failed entered the cache: the recovering read answers
        # with the derived profiles, not the credit-less people a swallowed
        # failure used to be able to leave.
        people = catalogue.people()

        self.assertTrue(people)
        self.assertTrue(any(person["relationships"] for person in people))

    def test_an_absent_sync_answers_empty_without_reading_other_authorities(self) -> None:
        EngineContentSource.objects.filter(slug=catalogue.PEOPLE_SOURCE_SLUG).update(
            is_enabled=False
        )

        with CaptureQueriesContext(connection) as read:
            people = catalogue.people()

        # The stamp aggregate is the only query: an absent source is an empty
        # profiles collection, not a row read against the editorial or event
        # tables its derivation would otherwise draw from.
        self.assertEqual(len(read), 1)
        self.assertEqual(people, ())


class SyncedMediaBindingTests(CacheBindingTestBase):
    """The same guarantees for the synced media authority (#384).

    The records are one collection drawn from two sources -- the editorial
    tree files and the profile pictures -- so the cache key carries a stamp
    for each, and a write to either side must move the merged answer.
    """

    def setUp(self) -> None:
        super().setUp()
        catalogue._synced_records.cache_clear()
        catalogue._synced_media.cache_clear()
        catalogue._media_index.cache_clear()

    def test_a_failed_row_read_raises_and_the_next_read_recovers(self) -> None:
        stamps = (
            catalogue.synced_stamp(catalogue.EDITORIAL_SOURCE_SLUG),
            catalogue.synced_stamp(catalogue.PEOPLE_SOURCE_SLUG),
        )
        real_filter = SyncedDocument.objects.filter
        failures = iter([OperationalError("media row read lost")])

        def flaky_filter(*args, **kwargs):
            try:
                raise next(failures)
            except StopIteration:
                return real_filter(*args, **kwargs)

        with (
            mock.patch.object(catalogue, "synced_stamp", lambda slug: stamps),
            mock.patch.object(SyncedDocument.objects, "filter", flaky_filter),
        ):
            with self.assertRaises(OperationalError):
                catalogue.media()

        # Nothing that failed entered the cache: the recovering read answers
        # with the published records, not the empty collection a swallowed
        # failure used to leave.
        with mock.patch.object(catalogue, "synced_stamp", lambda slug: stamps):
            self.assertTrue(catalogue.media())

    def test_a_sync_write_moves_the_stamp_and_the_next_read_sees_it(self) -> None:
        before = {record["record_key"]: record for record in catalogue.media()}
        people_key = next(key for key in before if key.startswith("images/authors/"))

        row = SyncedDocument.objects.get(
            source__slug=catalogue.PEOPLE_SOURCE_SLUG, stable_key=people_key
        )
        row.record = {**row.record, "content_type": "image/renamed"}
        row.save()

        after = {record["record_key"]: record for record in catalogue.media()}

        self.assertEqual(after[people_key]["content_type"], "image/renamed")

    def test_an_absent_source_answers_empty_without_reading_rows(self) -> None:
        EngineContentSource.objects.filter(
            slug__in=(catalogue.EDITORIAL_SOURCE_SLUG, catalogue.PEOPLE_SOURCE_SLUG)
        ).update(is_enabled=False)

        with CaptureQueriesContext(connection) as read:
            media = catalogue.media()

        # One stamp aggregate per source and nothing more: two absent sources
        # are an empty collection, not row reads.
        self.assertEqual(len(read), 2)
        self.assertEqual(media, ())
