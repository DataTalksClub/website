"""Regression coverage for the independent launch review findings."""

from datetime import timedelta

from community_base.jobs.models import JobIntent
from django.test import TestCase
from django.utils import timezone

from courses.announcement_jobs import dispatch_announcement
from courses.models import Announcement, AnnouncementAssignment
from courses.services.launch_queries import read_launch
from courses.services.launch_validation import LaunchError
from courses.tests.launch_support import LaunchFixture


class CourseLaunchReviewTests(LaunchFixture, TestCase):
    def test_cancelled_row_stays_cancelled_on_start_change_and_can_be_recovered(self):
        row = self.generated()
        self.command("cancel", id=str(row.pk), expected_revision=row.revision)
        self.update_start((self.start + timedelta(days=1)).isoformat())
        row.refresh_from_db()
        self.assertEqual(row.status, "cancelled")
        self.command(
            "announcement_edit",
            id=str(row.pk),
            expected_revision=row.revision,
            subject="Reviewed",
            body="Updated copy",
        )
        row.refresh_from_db()
        self.assertEqual(row.status, "draft")
        self.assertEqual(row.anchor_at, self.start + timedelta(days=1))
        self.approve(row)
        result = self.command(
            "schedule",
            send=True,
            id=str(row.pk),
            expected_revision=row.revision,
            confirmed=True,
            scheduled_at=(self.start + timedelta(days=1)).isoformat(),
        )
        self.assertEqual(result["status"], "scheduled")

    def test_scheduled_copy_can_be_rescheduled_directly_and_old_job_is_fenced(self):
        self.module.starts_at = timezone.now() - timedelta(days=1)
        self.module.save()
        row = self.generated()
        self.assertEqual(row.reason_code, "past_due")
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
            "schedule",
            send=True,
            id=str(row.pk),
            expected_revision=row.revision,
            confirmed=True,
            scheduled_at=(self.start + timedelta(days=2)).isoformat(),
        )
        row.refresh_from_db()
        self.assertEqual(row.approved_revision, row.revision)
        self.assertEqual(row.reason_code, "")
        dispatch_announcement(None, {"announcement_id": str(row.pk), "revision": old_revision})
        self.assertFalse(row.deliveries.exists())
        self.assertEqual(JobIntent.objects.filter(handler="courses.announcement").count(), 2)

    def test_edit_rebuilds_derived_context_and_preserves_only_authored_overrides(self):
        version = self.published()
        result = self.command(
            "compose",
            cohort_id=str(self.cohort.pk),
            version_id=str(version.pk),
            target_kind="module",
            target_id=str(self.module.pk),
            context={"module_intro_paragraph": "Authored introduction"},
        )
        row = Announcement.objects.get(pk=result["id"])
        new_start = self.start + timedelta(days=1)
        self.update_start(new_start.isoformat())
        self.command(
            "announcement_edit",
            id=str(row.pk),
            expected_revision=row.revision,
            subject="Reviewed",
            body="{{ starts_at }} {{ module_intro_paragraph }}",
        )
        row.refresh_from_db()
        self.assertEqual(row.context["starts_at"], new_start.isoformat())
        self.assertEqual(row.context_overrides, {"module_intro_paragraph": "Authored introduction"})
        self.assertEqual(row.context["module_intro_paragraph"], "Authored introduction")

    def test_oversized_telegram_template_is_rejected(self):
        with self.assertRaises(LaunchError):
            self.command(
                "template_save", name="Too long", channel="telegram", kind="module", body="X" * 4097
            )

    def test_long_generated_telegram_preview_does_not_poison_listing(self):
        draft = self.command(
            "template_save",
            name="Expanded copy",
            channel="telegram",
            kind="module",
            body="{{ module_title }}" * 25,
        )
        version = self.command("template_publish", id=draft["id"], expected_revision=1)
        assignment = AnnouncementAssignment.objects.create(course=self.course)
        assignment.versions.add(version["id"])
        self.module.title = "X" * 200
        self.module.save()
        self.command("launch", cohort_id=str(self.cohort.pk))
        listing = read_launch({"resource": "announcements"})
        self.assertEqual(listing["total_count"], 1)
        self.assertIn("4096", listing["items"][0]["preview"]["error"])
        self.assertTrue(listing["items"][0]["unarmed"])
        row = Announcement.objects.get()
        with self.assertRaises(LaunchError):
            self.approve(row)
        self.assertFalse(JobIntent.objects.filter(handler="courses.announcement").exists())

    def test_telegram_assignment_accepts_references_without_retaining_destinations(self):
        for value in ("12345:synthetic-token", "https://example.invalid/private", "-10012345"):
            with self.assertRaises(LaunchError):
                self.command(
                    "assignment_save",
                    course_id=str(self.course.pk),
                    expected_revision=1,
                    version_ids=[],
                    telegram_destination_key=value,
                )
        self.assertFalse(AnnouncementAssignment.objects.exists())
