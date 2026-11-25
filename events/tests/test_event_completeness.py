"""Every event must have a type, a speaker, a description and a picture.

The write paths refuse a missing type or speaker today (``test_publication``);
this module covers the full four-part rule where it is held beyond them: the
report (:func:`events.publication.incomplete_events`), the ``dtc_events.W001``
database system check, the ``/health/ready`` report, and -- on PostgreSQL only
-- the deferred constraint triggers of ``event_registrants`` migration 0005.

Run the PostgreSQL half with ``DJANGO_SETTINGS_MODULE=website.settings.test_postgres``
against a PostgreSQL server (see the ``postgres-confirm-regression`` CI job).
"""

from __future__ import annotations

import importlib
import unittest

from community_base.events.models import Event, EventHost
from django.core.checks import run_checks
from django.db import IntegrityError, connection, transaction
from django.test import TestCase

from event_registrants.models import EventOperatorConfig
from events.identity import create_event_identity
from events.publication import completeness_gaps, completeness_summary, incomplete_events

TRIGGERS = importlib.import_module("event_registrants.migrations.0005_event_completeness_triggers")


def _event(
    *,
    title: str = "Complete Event",
    description: str = "What the event is about.",
    picture: bytes | None = b"\x89PNG-bytes",
) -> Event:
    event = create_event_identity(
        title=title,
        event_type="webinar",
        speakers=[("janedoe", "Jane Doe")],
        fields={"description": description},
    )
    if picture is not None:
        EventOperatorConfig.objects.create(
            event=event, banner_image=picture, banner_content_type="image/png"
        )
    return event


class _WithoutSeedEvents(TestCase):
    """The owned test database is seeded with reference events; start from none."""

    def setUp(self) -> None:
        super().setUp()
        Event.objects.all().delete()


class CompletenessReportTests(_WithoutSeedEvents):
    def test_a_complete_event_is_not_reported(self) -> None:
        event = _event()
        self.assertEqual(completeness_gaps(event), ())
        self.assertEqual(incomplete_events(), [])

    def test_each_missing_part_is_reported(self) -> None:
        no_picture = _event(title="No Picture", picture=None)
        no_description = _event(title="No Description", description="  ")
        empty_picture = _event(title="Empty Picture", picture=b"")
        untyped = _event(title="Untyped")
        Event.objects.filter(pk=untyped.pk).update(tags=[])
        speakerless = _event(title="Speakerless")
        EventHost.objects.filter(event=speakerless).delete()

        report = {row["id"]: row["missing"] for row in incomplete_events()}
        self.assertEqual(report[no_picture.pk], ["picture"])
        self.assertEqual(report[no_description.pk], ["description"])
        self.assertEqual(report[empty_picture.pk], ["picture"])
        self.assertEqual(report[untyped.pk], ["type"])
        self.assertEqual(report[speakerless.pk], ["speakers"])
        speakerless.refresh_from_db()
        self.assertEqual(completeness_gaps(speakerless), ("speakers",))

    def test_drafts_are_held_to_the_rule_too(self) -> None:
        draft = _event(title="Draft Without Picture", picture=None)
        Event.objects.filter(pk=draft.pk).update(status="draft")
        self.assertEqual(
            [(row["id"], row["status"]) for row in incomplete_events()], [(draft.pk, "draft")]
        )

    def test_the_database_check_warns_while_an_event_is_incomplete(self) -> None:
        _event()
        self.assertEqual(
            [m.id for m in run_checks(databases=["default"]) if m.id == "dtc_events.W001"], []
        )
        incomplete = _event(title="No Picture", picture=None)
        errors = [m for m in run_checks(databases=["default"]) if m.id == "dtc_events.W001"]
        self.assertEqual(len(errors), 1)
        self.assertIn("picture: 1", errors[0].msg)
        self.assertIn(str(incomplete.public_id), errors[0].hint or "")

    def test_the_check_does_not_run_without_a_database(self) -> None:
        _event(title="No Picture", picture=None)
        self.assertEqual([m for m in run_checks() if m.id == "dtc_events.W001"], [])

    def test_readiness_reports_incomplete_events_without_failing(self) -> None:
        import core.views

        core.views._event_completeness_cache = None
        self.addCleanup(setattr, core.views, "_event_completeness_cache", None)
        _event(title="No Picture", picture=None)
        response = self.client.get("/health/ready")
        report = response.json()["checks"]["event_completeness"]
        self.assertEqual(report["status"], "violations")
        self.assertEqual(report["missing"]["picture"], 1)
        self.assertEqual(response.json()["status"], "ready")

    def test_summary_lists_public_ids_not_people(self) -> None:
        _event(title="No Picture", picture=None)
        summary = completeness_summary()
        self.assertEqual(set(summary["events"][0]), {"public_id", "id", "missing"})


@unittest.skipUnless(connection.vendor == "postgresql", "constraint triggers are PostgreSQL-only")
class CompletenessTriggerTests(_WithoutSeedEvents):
    """The triggers, enabled for the test, refuse a commit that leaves an event incomplete."""

    def setUp(self) -> None:
        super().setUp()
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT tgname, tgenabled FROM pg_trigger WHERE tgname LIKE 'dtc_event_complete%%'"
                " ORDER BY tgname"
            )
            self.migrated_state = cursor.fetchall()
            cursor.execute(TRIGGERS.ENABLE_SQL)

    def _commit_point(self) -> None:
        """Fire the deferred triggers now, as COMMIT would."""

        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            cursor.execute("SET CONSTRAINTS ALL DEFERRED")

    def _assert_refused(self, write) -> None:
        with self.assertRaises(IntegrityError) as caught, transaction.atomic():
            write()
            self._commit_point()
        self.assertIn(
            "must have a type, a speaker, a description and a picture", str(caught.exception)
        )

    def test_installed_disabled_until_the_backfill(self) -> None:
        self.assertEqual(
            self.migrated_state,
            [
                ("dtc_event_complete", "D"),
                ("dtc_event_complete_picture", "D"),
                ("dtc_event_complete_speakers", "D"),
            ],
        )

    def test_a_complete_event_commits(self) -> None:
        with transaction.atomic():
            _event()
            self._commit_point()

    def test_an_event_without_a_picture_cannot_be_committed(self) -> None:
        self._assert_refused(lambda: _event(picture=None))

    def test_an_event_without_a_description_cannot_be_committed(self) -> None:
        self._assert_refused(lambda: _event(description=""))

    def test_an_event_without_a_speaker_cannot_be_committed(self) -> None:
        def write() -> None:
            event = _event()
            EventHost.objects.filter(event=event).delete()

        self._assert_refused(write)

    def test_an_event_without_a_type_cannot_be_committed(self) -> None:
        def write() -> None:
            event = _event()
            Event.objects.filter(pk=event.pk).update(tags=["dtc-season:1"])

        self._assert_refused(write)

    def test_removing_the_last_speaker_or_the_picture_later_is_refused(self) -> None:
        with transaction.atomic():
            event = _event()
            self._commit_point()
        self._assert_refused(lambda: EventHost.objects.filter(event=event).delete())
        self._assert_refused(lambda: EventOperatorConfig.objects.filter(event=event).delete())
        self._assert_refused(
            lambda: EventOperatorConfig.objects.filter(event=event).update(banner_image=b"")
        )
        self._assert_refused(lambda: Event.objects.filter(pk=event.pk).update(description=" "))

    def test_deleting_a_whole_event_is_allowed(self) -> None:
        with transaction.atomic():
            event = _event()
            self._commit_point()
        with transaction.atomic():
            event.delete()
            self._commit_point()

    def test_the_sql_rule_matches_the_python_rule(self) -> None:
        # Incomplete rows must survive to the comparison, and TestCase teardown
        # sets constraints IMMEDIATE, so the triggers stay off for this one.
        with connection.cursor() as cursor:
            cursor.execute(TRIGGERS.DISABLE_SQL)
        events = [
            _event(title="Complete"),
            _event(title="No Picture", picture=None),
            _event(title="No Description", description=""),
        ]
        untyped = _event(title="Untyped")
        Event.objects.filter(pk=untyped.pk).update(tags=[])
        speakerless = _event(title="Speakerless")
        EventHost.objects.filter(event=speakerless).delete()
        for event in [*events, untyped, speakerless]:
            event.refresh_from_db()
            with connection.cursor() as cursor:
                cursor.execute("SELECT dtc_event_completeness_gaps(%s)", [event.pk])
                (sql_gaps,) = cursor.fetchone()
            self.assertEqual(tuple(sql_gaps), completeness_gaps(event), event.title)
