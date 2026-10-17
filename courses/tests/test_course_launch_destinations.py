"""Destination edits disarm pending work and cannot redirect a claimed send."""

from datetime import timedelta
from unittest.mock import patch

import requests
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from courses.announcement_jobs import dispatch_announcement
from courses.models import Announcement, Cohort
from courses.services.launch_transport import submit_telegram
from courses.services.launch_validation import LaunchError
from courses.tests.launch_support import LaunchFixture


def arm(row):
    row.refresh_from_db()
    row.status = "scheduled"
    row.approved_revision = row.revision
    row.scheduled_at = timezone.now() - timedelta(seconds=1)
    row.save()
    return row


def change_after_claim(assignment, row):
    assignment.telegram_destination_key = "after"
    assignment.save()
    return submit_telegram(row)


class CourseLaunchDestinationTests(LaunchFixture, TransactionTestCase):
    def change_key(self, key):
        assignment = self.course.announcement_assignment
        result = self.command(
            "assignment_save",
            course_id=str(self.course.pk),
            version_ids=[str(version.pk) for version in assignment.versions.all()],
            expected_revision=assignment.revision,
            telegram_destination_key=key,
        )
        assignment.refresh_from_db()
        return result

    def manual(self, cohort, status="scheduled", channel="telegram"):
        return Announcement.objects.create(
            cohort=cohort,
            channel=channel,
            target_kind="cohort",
            target_id=str(cohort.pk),
            subject="Synthetic update",
            body="Reviewed synthetic update",
            status=status,
            approved_revision=1,
            scheduled_at=timezone.now() - timedelta(seconds=1),
        )

    def test_course_destination_change_disarms_and_fences_generated_work(self):
        row = arm(self.generated("telegram"))
        revision = row.revision
        self.change_key("after")
        row.refresh_from_db()
        self.assertEqual((row.status, row.reason_code), ("draft", "destination_changed"))
        self.assertIsNone(row.approved_revision)
        self.assertGreater(row.revision, revision)
        with patch("courses.announcement_jobs.submit_telegram") as provider:
            dispatch_announcement(None, {"announcement_id": str(row.pk), "revision": revision})
        provider.assert_not_called()

    def test_template_override_and_manual_only_cohort_inherit_destination_change(self):
        row = arm(self.generated("telegram"))
        plan = self.cohort.announcement_plan
        plan.override_defaults = True
        plan.save()
        plan.versions.add(row.version)
        cohort = Cohort.objects.create(
            course=self.course,
            slug="manual-only",
            title="Manual only",
            year=2028,
            identifier="2028",
        )
        manual = self.manual(cohort)
        cancelled = self.manual(cohort, "cancelled")
        submitted = self.manual(cohort, "submitted")
        email = self.manual(cohort, channel="email")
        self.change_key("after")
        for pending in (row, manual):
            pending.refresh_from_db()
            self.assertEqual(pending.status, "draft")
            self.assertIsNone(pending.approved_revision)
        for preserved, expected in (
            (cancelled, "cancelled"),
            (submitted, "submitted"),
            (email, "scheduled"),
        ):
            preserved.refresh_from_db()
            self.assertEqual((preserved.status, preserved.revision), (expected, 1))

    def test_omitted_course_key_is_preserved_and_cohort_key_is_rejected(self):
        row = self.generated("telegram")
        self.change_key("before")
        arm(row)
        assignment = self.course.announcement_assignment
        self.command(
            "assignment_save",
            course_id=str(self.course.pk),
            version_ids=[str(row.version_id)],
            expected_revision=assignment.revision,
        )
        assignment.refresh_from_db()
        row.refresh_from_db()
        self.assertEqual(assignment.telegram_destination_key, "before")
        self.assertEqual(row.status, "scheduled")
        with self.assertRaises(LaunchError):
            self.command(
                "assignment_save",
                cohort_id=str(self.cohort.pk),
                version_ids=[str(row.version_id)],
                expected_revision=1,
                telegram_destination_key="unsupported",
            )

    @override_settings(
        COURSE_ANNOUNCEMENTS_ENABLED=True,
        ENVIRONMENT="production",
        COURSE_TELEGRAM_BOT_TOKEN="synthetic-only",
        COURSE_TELEGRAM_DESTINATIONS={"before": "synthetic-before", "after": "synthetic-after"},
    )
    def test_claim_snapshots_key_before_assignment_changes(self):
        row = self.generated("telegram")
        self.change_key("before")
        row.refresh_from_db()
        arm(row)
        assignment = self.course.announcement_assignment
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"ok": true, "result": {"message_id": 1}}'
        with (
            patch(
                "courses.announcement_jobs.submit_telegram",
                side_effect=lambda claimed: change_after_claim(assignment, claimed),
            ),
            patch(
                "courses.services.launch_transport.requests.post", return_value=response
            ) as provider,
        ):
            dispatch_announcement(None, {"announcement_id": str(row.pk), "revision": row.revision})
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(provider.call_args.kwargs["json"]["chat_id"], "synthetic-before")
