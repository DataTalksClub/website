"""Contending launch writers keep one revision winner and one transport intent."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch

import requests
from django.db import OperationalError, close_old_connections, connections, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from courses.announcement_jobs import dispatch_announcement
from courses.models import Announcement, CohortAnnouncementPlan
from courses.services.launch_transport import submit_telegram
from courses.services.launch_validation import LaunchError, load_row
from courses.tests.launch_support import LaunchFixture


def outcome(callback, position):
    try:
        return callback(position)
    except (LaunchError, OperationalError) as error:
        return error


def run_pair(callback):
    barrier = Barrier(2)

    def run(position):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return outcome(callback, position)
        finally:
            connections["default"].close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        return list(pool.map(run, [0, 1]))


class CourseLaunchConcurrencyTests(LaunchFixture, TransactionTestCase):
    def test_concurrent_edits_have_one_revision_winner(self):
        row = self.generated()

        def edit(position):
            return self.command(
                "announcement_edit",
                id=str(row.pk),
                expected_revision=row.revision,
                subject=f"Winner {position}",
                body="Reviewed update",
            )

        results = run_pair(edit)
        results = [
            outcome(edit, index) if isinstance(item, OperationalError) else item
            for index, item in enumerate(results)
        ]
        winners = [result for result in results if isinstance(result, dict)]
        self.assertEqual(len(winners), 1, results)
        conflicts = [item for item in results if isinstance(item, LaunchError)]
        self.assertEqual(len(conflicts), 1, results)
        self.assertEqual(conflicts[0].code, "revision_conflict")
        row.refresh_from_db()
        self.assertEqual(row.revision, winners[0]["revision"])
        self.assertEqual(row.subject, winners[0]["subject"])
        self.assertIsNone(row.approved_revision)

    def test_concurrent_first_launch_creates_one_plan_and_announcement(self):
        version = self.published()
        self.command(
            "assignment_save",
            course_id=str(self.course.pk),
            version_ids=[str(version.pk)],
            expected_revision=1,
        )
        results = run_pair(lambda position: self.command("launch", cohort_id=str(self.cohort.pk)))
        self.assertTrue(any(isinstance(result, dict) for result in results), results)
        self.assertFalse(any(isinstance(result, LaunchError) for result in results), results)
        self.assertEqual(CohortAnnouncementPlan.objects.filter(cohort=self.cohort).count(), 1)
        self.assertEqual(Announcement.objects.filter(cohort=self.cohort).count(), 1)

    def test_failure_after_serialization_rolls_back_status_and_revision(self):
        row = self.generated()
        revision = row.revision
        with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
            with transaction.atomic():
                current = load_row(Announcement, row.pk, lock=True)
                current.status = "cancelled"
                current.revision += 1
                current.save(update_fields=["status", "revision"])
                raise RuntimeError("synthetic failure")
        row.refresh_from_db()
        self.assertEqual((row.status, row.revision), ("draft", revision))

    def test_concurrent_dispatch_claims_submit_once(self):
        row = self.generated()
        row.status = "scheduled"
        row.scheduled_at = timezone.now() - timedelta(seconds=1)
        row.save()
        payload = {"announcement_id": str(row.pk), "revision": row.revision}
        with patch("courses.announcement_jobs.submit_email") as provider:
            results = run_pair(lambda position: dispatch_announcement(None, payload))
        self.assertFalse(any(isinstance(result, LaunchError) for result in results))
        self.assertEqual(provider.call_count, 1)
        row.refresh_from_db()
        self.assertIn(row.status, {"submitted", "ambiguous"})

    @override_settings(
        COURSE_ANNOUNCEMENTS_ENABLED=True,
        ENVIRONMENT="production",
        COURSE_TELEGRAM_BOT_TOKEN="synthetic-only",
        COURSE_TELEGRAM_DESTINATIONS={"test": "synthetic-chat"},
    )
    def test_concurrent_telegram_claims_submit_once(self):
        row = self.generated("telegram")
        assignment = self.course.announcement_assignment
        assignment.telegram_destination_key = "test"
        assignment.save()
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"ok": true, "result": {"message_id": 1}}'
        with patch(
            "courses.services.launch_transport.requests.post", return_value=response
        ) as provider:
            run_pair(lambda position: submit_telegram(row))
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(row.deliveries.get().state, "submitted")
