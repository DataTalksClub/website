"""Recipient preferences, replay safety and the optional upstream start boundary."""

from datetime import timedelta
from unittest.mock import patch

import requests
from community_base.mail.models import EmailDelivery
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from courses.models import (
    Announcement,
    AnnouncementAssignment,
    AnnouncementDelivery,
    Cohort,
    Course,
    Enrollment,
    Homework,
    Module,
)
from courses.services.launch_import_timing import import_explicit_start
from courses.services.launch_transport import submit_email, submit_telegram


class CourseLaunchDeliveryTests(TestCase):
    def setUp(self):
        self.course = Course.objects.create(slug="delivery-test", title="Delivery test")
        self.cohort = Cohort.objects.create(
            course=self.course, slug="delivery-2027", title="Delivery 2027"
        )
        self.row = Announcement.objects.create(
            cohort=self.cohort,
            channel="email",
            target_id=str(self.cohort.pk),
            subject="A course update",
            body="Reviewed message",
        )
        self.user = get_user_model().objects.create_user(
            username="synthetic-recipient", email="synthetic@example.invalid"
        )
        Enrollment.objects.create(course=self.cohort, student=self.user)

    @override_settings(COURSE_ANNOUNCEMENTS_ENABLED=True, ENVIRONMENT="production")
    def test_email_is_one_logical_relay_intent_and_honors_preference(self):
        submit_email(self.row)
        submit_email(self.row)
        delivery = EmailDelivery.objects.get(related_object_id=str(self.row.pk))
        self.assertEqual(delivery.sender_id, "courses")
        self.assertEqual(delivery.category, "course-updates")
        self.assertEqual(
            delivery.idempotency_key, f"announcement:{self.row.pk}:user:{self.user.pk}"
        )
        self.assertEqual(AnnouncementDelivery.objects.filter(announcement=self.row).count(), 1)
        self.user.email_course_updates = False
        self.user.save()
        self.row.pk = None
        self.row.save()
        submit_email(self.row)
        self.assertEqual(self.row.deliveries.get().state, "suppressed")
        self.assertEqual(EmailDelivery.objects.count(), 1)

    @override_settings(COURSE_ANNOUNCEMENTS_ENABLED=True, ENVIRONMENT="development")
    def test_development_remains_simulated_even_when_enabled(self):
        with patch("courses.services.launch_transport.send") as provider:
            submit_email(self.row)
        provider.assert_not_called()
        self.assertEqual(self.row.deliveries.get().state, "simulated")

    def test_optional_start_import_preserves_local_edits_and_explicit_clear(self):
        start = timezone.now() + timedelta(days=10)
        import_explicit_start(self.cohort, {}, "cohort")
        self.assertIsNone(self.cohort.starts_at)

        import_explicit_start(self.cohort, {"starts_at": start.isoformat()}, "cohort")
        self.cohort.refresh_from_db()
        self.assertEqual(self.cohort.starts_at, start)
        self.cohort.starts_at = None
        self.cohort.timing_revision = 1
        self.cohort.save()
        import_explicit_start(self.cohort, {"starts_at": start.isoformat()}, "cohort")
        self.cohort.refresh_from_db()
        self.assertIsNone(self.cohort.starts_at)

    def test_homework_start_propagates_only_to_explicit_binding(self):
        start = timezone.now() + timedelta(days=10)
        homework = Homework.objects.create(
            course=self.cohort, slug="hw1", title="Homework", due_date=start + timedelta(days=7)
        )
        bound = Module.objects.create(
            cohort=self.cohort, terminal_homework=homework, slug="bound", title="Bound", position=1
        )
        import_explicit_start(homework, {"starts_at": start.isoformat()}, "homework")
        bound.refresh_from_db()
        self.assertEqual(bound.starts_at, start)
        self.assertEqual(homework.due_date, start + timedelta(days=7))

    @override_settings(
        COURSE_ANNOUNCEMENTS_ENABLED=True,
        ENVIRONMENT="production",
        COURSE_TELEGRAM_BOT_TOKEN="synthetic-only",
        COURSE_TELEGRAM_DESTINATIONS={"test": "synthetic-chat"},
    )
    def test_malformed_telegram_ack_is_ambiguous_and_never_retried(self):
        AnnouncementAssignment.objects.create(course=self.course, telegram_destination_key="test")
        self.row.channel = "telegram"
        self.row.save()
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"ok": true, "result": {}}'
        with patch(
            "courses.services.launch_transport.requests.post", return_value=response
        ) as provider:
            self.assertEqual(submit_telegram(self.row), "ambiguous")
            self.assertEqual(submit_telegram(self.row), "ambiguous")
        self.assertEqual(provider.call_count, 1)
