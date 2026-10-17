"""Launch behavior is tested at its shared service boundary; no network transports."""

from datetime import timedelta
from unittest.mock import patch

from community_base.jobs.models import JobIntent
from django.test import TestCase, override_settings
from django.utils import timezone

from core.idempotency import IdempotencyConflict
from courses.announcement_jobs import dispatch_announcement
from courses.models import (
    Announcement,
    AnnouncementAssignment,
    AnnouncementDelivery,
)
from courses.services.launch_validation import LaunchError
from courses.tests.launch_support import LaunchFixture


class CourseLaunchTests(LaunchFixture, TestCase):
    def test_launch_generates_unarmed_start_relative_rows_idempotently(self):
        row = self.generated()
        self.assertEqual(row.status, "draft")
        self.assertEqual(row.scheduled_at, self.module.starts_at)
        self.assertFalse(JobIntent.objects.filter(handler="courses.announcement").exists())
        self.command("launch", cohort_id=str(self.cohort.pk))
        self.assertEqual(Announcement.objects.count(), 1)

    def test_self_paced_and_archive_fail_closed_without_rows(self):
        for field, value in (
            ("delivery_mode", "self_paced"),
            ("curriculum_source", "github_archive"),
        ):
            if field == "curriculum_source":
                self.cohort.archive_notice_path = "cohorts/2027/README.md"
                self.cohort.archive_url = (
                    "https://github.com/example/course/blob/" + "a" * 40 + "/cohorts/2027/README.md"
                )
                self.cohort.archive_commit_sha = "a" * 40
            setattr(self.cohort, field, value)
            self.cohort.save()
            with self.assertRaises(LaunchError):
                self.command("launch", cohort_id=str(self.cohort.pk))
            self.assertFalse(Announcement.objects.exists())
            setattr(self.cohort, field, "live" if field == "delivery_mode" else "current")

    def test_schedule_requires_approval_and_confirmation_and_replay_is_one_job(self):
        row = self.generated()
        with self.assertRaises(LaunchError):
            self.command(
                "schedule",
                send=True,
                id=str(row.pk),
                expected_revision=row.revision,
                confirmed=True,
                scheduled_at=self.start.isoformat(),
            )
        self.approve(row)
        values = dict(
            send=True,
            key="schedule-repeat",
            id=str(row.pk),
            expected_revision=row.revision,
            confirmed=True,
            scheduled_at=self.start.isoformat(),
        )
        first = self.command("schedule", **values)
        second = self.command("schedule", **values)
        self.assertEqual(first["id"], second["id"])
        self.assertTrue(second["replayed"])
        self.assertEqual(JobIntent.objects.filter(handler="courses.announcement").count(), 1)

    def test_start_changes_clear_approval_and_old_job_is_fenced(self):
        row = self.generated()
        self.approve(row)
        self.command(
            "schedule",
            send=True,
            id=str(row.pk),
            expected_revision=row.revision,
            confirmed=True,
            scheduled_at=self.start.isoformat(),
        )
        row.refresh_from_db()
        old_revision = row.revision
        self.command(
            "timing_update",
            id=str(self.module.pk),
            target_kind="module",
            starts_at=(self.start + timedelta(days=1)).isoformat(),
            provenance="Reviewed schedule",
            expected_revision=0,
        )
        row.refresh_from_db()
        self.assertEqual(row.status, "draft")
        self.assertIsNone(row.approved_revision)
        dispatch_announcement(None, {"announcement_id": str(row.pk), "revision": old_revision})
        self.assertFalse(AnnouncementDelivery.objects.exists())
        self.homework.refresh_from_db()
        self.assertEqual(self.homework.due_date, self.start + timedelta(days=7))

    def test_missing_naive_and_late_starts_and_stale_writes(self):
        for value in ("2027-01-01T12:00:00", (self.start + timedelta(days=8)).isoformat()):
            with self.assertRaises(LaunchError):
                self.update_start(value)
        self.update_start(None)
        with self.assertRaises(LaunchError) as caught:
            self.update_start(None)
        self.assertEqual(caught.exception.status, 409)
        row = self.generated()
        self.assertEqual(row.reason_code, "missing_start")

    def test_published_version_remains_immutable_after_edit(self):
        version = self.published()
        self.command(
            "template_save",
            id=str(version.template_id),
            expected_revision=1,
            name="Edited",
            channel="email",
            kind="module",
            subject="Changed",
            body="Changed",
        )
        version.refresh_from_db()
        self.assertIn("module_title", version.subject)

    def test_edit_and_cancel_fence_queued_job(self):
        row = self.generated()
        self.approve(row)
        self.command(
            "schedule",
            send=True,
            id=str(row.pk),
            expected_revision=row.revision,
            confirmed=True,
            scheduled_at=self.start.isoformat(),
        )
        row.refresh_from_db()
        self.command(
            "announcement_edit",
            id=str(row.pk),
            expected_revision=row.revision,
            subject="Updated",
            body="Updated",
        )
        row.refresh_from_db()
        self.assertEqual(row.status, "draft")
        self.command("cancel", id=str(row.pk), expected_revision=row.revision)
        row.refresh_from_db()
        self.assertEqual(row.status, "cancelled")

    @override_settings(
        COURSE_ANNOUNCEMENTS_ENABLED=True,
        ENVIRONMENT="production",
        COURSE_TELEGRAM_DESTINATIONS={"test": "synthetic"},
        COURSE_TELEGRAM_BOT_TOKEN="synthetic-test-only",
    )
    def test_telegram_uncertain_ack_is_quarantined_without_retry(self):
        import requests

        row = self.generated("telegram")
        assignment = AnnouncementAssignment.objects.get(course=self.course)
        assignment.telegram_destination_key = "test"
        assignment.save()
        row.status = "scheduled"
        row.scheduled_at = timezone.now()
        row.save()
        payload = {"announcement_id": str(row.pk), "revision": row.revision}
        with patch(
            "courses.services.launch_transport.requests.post", side_effect=requests.Timeout
        ) as transport:
            dispatch_announcement(None, payload)
            dispatch_announcement(None, payload)
        self.assertEqual(transport.call_count, 1)
        row.refresh_from_db()
        self.assertEqual(row.status, "ambiguous")

    def test_idempotency_conflict_rolls_back_different_command(self):
        self.command(
            "template_save",
            key="same",
            name="A",
            channel="email",
            kind="module",
            subject="One",
            body="Two",
        )
        with self.assertRaises(IdempotencyConflict):
            self.command(
                "template_save",
                key="same",
                name="B",
                channel="email",
                kind="module",
                subject="One",
                body="Two",
            )

    def test_assignment_change_cancels_old_generated_work(self):
        row = self.generated()
        assignment = AnnouncementAssignment.objects.get(course=self.course)
        self.command(
            "assignment_save",
            course_id=str(self.course.pk),
            expected_revision=assignment.revision,
            version_ids=[],
        )
        row.refresh_from_db()
        self.assertEqual(row.status, "cancelled")
        self.assertIsNone(row.approved_revision)

    def test_project_schedule_uses_start_instead_of_deadline(self):
        version = self.published(kind="project")
        assignment = AnnouncementAssignment.objects.create(course=self.course)
        assignment.versions.add(version)
        self.command("launch", cohort_id=str(self.cohort.pk))
        row = Announcement.objects.get()
        self.assertEqual(row.scheduled_at, self.project.starts_at)
        self.assertNotEqual(row.scheduled_at, self.project.submission_due_date)
