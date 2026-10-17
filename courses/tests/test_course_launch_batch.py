"""Batch launch-and-schedule: preview contract, confirmed execute, skips, idempotency."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch

from community_base.jobs.models import JobIntent
from django.contrib.auth.models import Permission
from django.db import OperationalError, close_old_connections, connections
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from accounts.studio_test_support import authenticated_studio_client, make_studio_user
from core.models import IdempotencyRecord
from courses.models import (
    Announcement,
    AnnouncementAssignment,
    AnnouncementDelivery,
    Cohort,
    CohortAnnouncementPlan,
    CohortSharedModule,
    Course,
    Homework,
    Module,
    Project,
    SharedCurriculum,
    SharedModule,
)
from courses.services.launch_batch import verify_preview_token
from courses.services.launch_queries import read_launch
from courses.services.launch_validation import LaunchError
from courses.tests.launch_support import LaunchFixture
from management_auth.models import APICredential, APIPrincipal
from management_auth.tokens import encode_secret, generate_token

SHA = "a" * 40
CHECKSUM = "b" * 64


def provenance(content_id, path):
    return {
        "source_content_id": content_id,
        "source_path": path,
        "source_commit_sha": SHA,
        "source_checksum": CHECKSUM,
    }


class BatchPreviewTests(LaunchFixture, TestCase):
    def preview(self, cohort=None):
        return read_launch(
            {
                "resource": "batch_preview",
                "cohort_id": str((cohort or self.cohort).pk),
            }
        )

    def execute(self, preview, key=None, **overrides):
        import uuid

        payload = {
            "cohort_id": preview["cohort_id"],
            "confirmed": True,
            "preview_token": preview["preview_token"],
        }
        payload.update(overrides)
        return self.command(
            "launch_and_schedule", send=True, key=key or str(uuid.uuid4()), **payload
        )

    def jobs(self):
        return JobIntent.objects.filter(handler="courses.announcement")

    def test_dry_run_before_any_plan_exists_writes_nothing(self):
        version = self.published()
        assignment = AnnouncementAssignment.objects.create(course=self.course)
        assignment.versions.add(version)
        records_before = IdempotencyRecord.objects.count()
        preview = self.preview()
        self.assertFalse(CohortAnnouncementPlan.objects.filter(cohort=self.cohort).exists())
        self.assertFalse(Announcement.objects.exists())
        self.assertFalse(self.jobs().exists())
        self.assertEqual(IdempotencyRecord.objects.count(), records_before)
        self.assertIsNone(preview["guidance"])
        self.assertIn("preview_token", preview)
        digest, records = verify_preview_token(preview["preview_token"])
        self.assertEqual(digest, preview["preview_fingerprint"])
        self.assertTrue(records)
        prospective = [item for item in preview["rows"] if item["status"] == "prospective"]
        self.assertTrue(prospective)
        self.assertTrue(all(item["reason"] == "eligible" for item in prospective))
        self.assertEqual(preview["eligible_count"], len(prospective))

    def test_dry_run_without_any_versions_reports_guidance(self):
        preview = self.preview()
        self.assertEqual(preview["eligible_count"], 0)
        self.assertIsNotNone(preview["guidance"])
        self.assertFalse(Announcement.objects.exists())
        self.assertFalse(self.jobs().exists())

    def test_preview_token_is_tamper_evident(self):
        self.generated()
        preview = self.preview()
        token = preview["preview_token"]
        tampered = token[:-2] + ("aa" if token[-2:] != "aa" else "bb")
        with self.assertRaises(LaunchError):
            self.execute(preview, preview_token=tampered)
        with self.assertRaises(LaunchError):
            self.execute(preview, preview_token="not-a-signed-token")
        with self.assertRaises(LaunchError):
            self.execute(preview, preview_token="")
        self.assertFalse(self.jobs().exists())
        self.assertEqual(Announcement.objects.get().status, "draft")

    def test_editable_client_maps_are_rejected(self):
        self.generated()
        preview = self.preview()
        with self.assertRaises(LaunchError) as raised:
            self.command(
                "launch_and_schedule",
                send=True,
                cohort_id=preview["cohort_id"],
                confirmed=True,
                preview_token=preview["preview_token"],
                preview_rows=[],
            )
        self.assertIn("unsupported", str(raised.exception).lower())
        with self.assertRaises(LaunchError):
            self.command(
                "launch_and_schedule",
                send=True,
                cohort_id=preview["cohort_id"],
                confirmed=True,
                preview_token=preview["preview_token"],
                preview_fingerprint=preview["preview_fingerprint"],
            )
        self.assertFalse(self.jobs().exists())

    def test_confirm_schedules_all_future_rows_once(self):
        row = self.generated()
        preview = self.preview()
        self.assertEqual(preview["eligible_count"], 1)
        key = preview["rows"][0]["generated_key"]
        result = self.execute(preview)
        self.assertEqual(result["enqueued_count"], 1)
        self.assertEqual(result["skipped_count"], 0)
        entry = result["enqueued"][0]
        self.assertEqual(entry["generated_key"], key)
        self.assertEqual(entry["scheduled_at"], row.anchor_at.isoformat())
        row.refresh_from_db()
        self.assertEqual(row.status, "scheduled")
        self.assertEqual(row.approved_revision, row.revision)
        self.assertEqual(self.jobs().count(), 1)
        intent = self.jobs().get()
        self.assertGreater(intent.available_at, timezone.now())
        self.assertEqual(intent.payload["announcement_id"], str(row.pk))
        self.assertEqual(intent.payload["revision"], row.revision)

    def test_approved_row_schedules_without_reapprove(self):
        row = self.generated()
        self.approve(row)
        row.refresh_from_db()
        self.assertEqual(row.status, "approved")
        preview = self.preview()
        self.assertEqual(preview["eligible_count"], 1)
        revision_before = row.revision
        result = self.execute(preview)
        self.assertEqual(result["enqueued_count"], 1)
        row.refresh_from_db()
        self.assertEqual(row.status, "scheduled")
        # Scheduled directly without a second approval: exactly one new revision.
        self.assertEqual(row.revision, revision_before + 1)
        self.assertEqual(row.approved_revision, row.revision)
        self.assertEqual(self.jobs().count(), 1)

    def test_version_change_rejects_whole_batch_and_key_is_reusable(self):
        self.generated()
        preview = self.preview()
        key = "batch-version-change"
        fresh = self.published()
        assignment = AnnouncementAssignment.objects.get(course=self.course)
        assignment.versions.add(fresh)
        records_before = IdempotencyRecord.objects.count()
        with self.assertRaises(LaunchError) as raised:
            self.execute(preview, key=key)
        self.assertEqual(raised.exception.status, 409)
        self.assertFalse(self.jobs().exists())
        self.assertEqual(IdempotencyRecord.objects.count(), records_before)
        for row in Announcement.objects.all():
            self.assertNotEqual(row.status, "scheduled")
        corrected = self.preview()
        self.assertNotEqual(corrected["preview_fingerprint"], preview["preview_fingerprint"])
        result = self.execute(corrected, key=key)
        self.assertEqual(result["enqueued_count"], 2)
        self.assertEqual(self.jobs().count(), 2)

    def test_destination_change_rejects_whole_batch(self):
        self.generated()
        preview = self.preview()
        assignment = AnnouncementAssignment.objects.get(course=self.course)
        self.command(
            "assignment_save",
            course_id=str(self.course.pk),
            version_ids=[str(version.pk) for version in assignment.versions.all()],
            telegram_destination_key="primary",
            expected_revision=assignment.revision,
        )
        with self.assertRaises(LaunchError) as raised:
            self.execute(preview)
        self.assertEqual(raised.exception.status, 409)
        self.assertFalse(self.jobs().exists())

    def test_delivery_and_override_changes_reject_whole_batch(self):
        self.generated()
        preview = self.preview()
        self.cohort.delivery_mode = "self_paced"
        self.cohort.save()
        with self.assertRaises(LaunchError) as raised:
            self.execute(preview)
        self.assertEqual(raised.exception.status, 409)
        self.assertFalse(self.jobs().exists())
        self.cohort.delivery_mode = "live"
        self.cohort.save()
        plan = CohortAnnouncementPlan.objects.get(cohort=self.cohort)
        plan.override_defaults = True
        plan.save()
        with self.assertRaises(LaunchError) as flipped:
            self.execute(preview)
        self.assertEqual(flipped.exception.status, 409)
        self.assertFalse(self.jobs().exists())

    def test_single_edited_row_skips_stale_and_copy_stays_unapproved(self):
        first = self.published()
        second = self.published()
        assignment = AnnouncementAssignment.objects.create(course=self.course)
        assignment.versions.add(first, second)
        self.command("launch", cohort_id=str(self.cohort.pk))
        rows = {row.version_id: row for row in Announcement.objects.all()}
        self.assertEqual(len(rows), 2)
        preview = self.preview()
        before = Announcement.objects.get(pk=rows[first.pk].pk)
        before_subject, before_body, before_revision = (
            before.subject,
            before.body,
            before.revision,
        )
        self.command(
            "announcement_edit",
            id=str(rows[first.pk].pk),
            subject="Edited subject",
            body="Edited body",
            expected_revision=rows[first.pk].revision,
        )
        result = self.execute(preview)
        self.assertEqual(result["enqueued_count"], 1)
        self.assertEqual(result["skipped_count"], 1)
        skipped = result["skipped"][0]
        self.assertEqual(skipped["reason"], "stale_revision")
        edited = Announcement.objects.get(pk=rows[first.pk].pk)
        self.assertEqual(edited.status, "draft")
        self.assertIsNone(edited.approved_revision)
        # Never-approve-unseen proof: the edited copy is byte-identical post-execute.
        self.assertEqual(edited.subject, "Edited subject")
        self.assertEqual(edited.body, "Edited body")
        self.assertNotEqual((edited.subject, edited.body), (before_subject, before_body))
        self.assertGreater(edited.revision, before_revision)
        self.assertEqual(self.jobs().count(), 1)

    def test_single_moved_start_skips_start_changed(self):
        self.generated()
        preview = self.preview()
        moved = (self.start + timedelta(days=1)).isoformat()
        self.command(
            "timing_update",
            id=str(self.module.pk),
            target_kind="module",
            starts_at=moved,
            provenance="Moved start",
            expected_revision=0,
        )
        result = self.execute(preview)
        self.assertEqual(result["enqueued_count"], 0)
        self.assertEqual(result["skipped"][0]["reason"], "start_changed")
        self.assertFalse(self.jobs().exists())

    def make_module(self, position, slug, start):
        base = start if start is not None else timezone.now()
        homework = Homework.objects.create(
            course=self.cohort,
            slug=f"{slug}-hw",
            title=f"{slug} homework",
            due_date=base + timedelta(days=7),
        )
        return Module.objects.create(
            cohort=self.cohort,
            position=position,
            slug=slug,
            title=f"{slug} module",
            terminal_homework=homework,
            starts_at=start,
        )

    def test_new_target_between_preview_and_execute_skips_not_in_preview(self):
        self.generated()
        preview = self.preview()
        other = self.make_module(2, "two", self.start + timedelta(days=1))
        result = self.execute(preview)
        self.assertEqual(result["enqueued_count"], 1)
        self.assertEqual(result["skipped_count"], 1)
        skipped = result["skipped"][0]
        self.assertEqual(skipped["reason"], "not_in_preview")
        self.assertIn(str(other.pk), skipped["generated_key"])
        self.assertEqual(self.jobs().count(), 1)

    def test_mixed_cohort_skips_with_reasons(self):
        module_version = self.published()
        project_version = self.published(kind="project")
        telegram_version = self.published(channel="telegram")
        assignment = AnnouncementAssignment.objects.create(course=self.course)
        assignment.versions.add(module_version, project_version, telegram_version)
        past_module = self.make_module(9, "past", timezone.now() - timedelta(days=1))
        open_project = Project.objects.create(
            course=self.cohort,
            slug="open",
            title="Open project",
            submission_due_date=self.start + timedelta(days=14),
            peer_review_due_date=self.start + timedelta(days=21),
        )
        self.command("launch", cohort_id=str(self.cohort.pk))

        def key_for(version, kind, target):
            return f"{self.cohort.pk}:{version.pk}:{kind}:{target}"

        eligible_key = key_for(project_version, "project", self.project.pk)
        cancelled_key = key_for(module_version, "module", self.module.pk)
        settled_key = key_for(module_version, "module", past_module.pk)
        blocked_key = key_for(telegram_version, "module", self.module.pk)
        past_key = key_for(telegram_version, "module", past_module.pk)
        missing_key = key_for(project_version, "project", open_project.pk)
        manual = self.command(
            "compose",
            cohort_id=str(self.cohort.pk),
            version_id=str(module_version.pk),
            target_kind="module",
            target_id=str(self.module.pk),
        )
        rows = {row.generated_key: row for row in Announcement.objects.all()}
        self.assertIn(None, rows)
        self.command(
            "cancel",
            id=str(rows[cancelled_key].pk),
            expected_revision=rows[cancelled_key].revision,
        )
        Announcement.objects.filter(pk=rows[settled_key].pk).update(status="submitted")
        preview = self.preview()
        reasons = {item["generated_key"]: item["reason"] for item in preview["rows"]}
        self.assertEqual(reasons[eligible_key], "eligible")
        self.assertEqual(reasons[cancelled_key], "cancelled")
        self.assertEqual(reasons[settled_key], "settled")
        self.assertEqual(reasons[blocked_key], "blocked_destination")
        self.assertEqual(reasons[past_key], "past_due")
        self.assertEqual(reasons[missing_key], "missing_start")
        manual_items = [item for item in preview["rows"] if not item["generated_key"]]
        self.assertEqual(len(manual_items), 1)
        self.assertEqual(manual_items[0]["reason"], "manual")
        result = self.execute(preview)
        self.assertEqual(result["enqueued_count"], 1)
        self.assertEqual(result["enqueued"][0]["generated_key"], eligible_key)
        self.assertEqual(self.jobs().count(), 1)
        cancelled = Announcement.objects.get(pk=rows[cancelled_key].pk)
        self.assertEqual(cancelled.status, "cancelled")
        self.assertIsNone(cancelled.approved_revision)
        untouched = Announcement.objects.get(pk=manual["id"])
        self.assertEqual(untouched.status, "draft")
        self.assertIsNone(untouched.generated_key)

    def test_replay_returns_stored_result_and_new_key_skips_scheduled(self):
        self.generated()
        preview = self.preview()
        key = "batch-replay-key"
        first = self.execute(preview, key=key)
        self.assertFalse(first["replayed"])
        second = self.execute(preview, key=key)
        self.assertTrue(second["replayed"])
        self.assertEqual(second["enqueued_count"], first["enqueued_count"])
        self.assertEqual(self.jobs().count(), 1)
        third = self.execute(preview, key="batch-other-key")
        self.assertFalse(third["replayed"])
        self.assertEqual(third["enqueued_count"], 0)
        self.assertEqual(third["skipped"][0]["reason"], "already_scheduled")
        self.assertEqual(self.jobs().count(), 1)

    def test_forced_error_rolls_back_without_jobs(self):
        first = self.published()
        second = self.published()
        assignment = AnnouncementAssignment.objects.create(course=self.course)
        assignment.versions.add(first, second)
        self.command("launch", cohort_id=str(self.cohort.pk))
        preview = self.preview()
        records_before = IdempotencyRecord.objects.count()
        calls = []

        def explode(payload):
            calls.append(payload)
            if len(calls) > 1:
                raise RuntimeError("synthetic mid-atomic failure")
            from courses.services import launch_announcements

            return launch_announcements.schedule_announcement(payload)

        with patch("courses.services.launch_batch.schedule_announcement", side_effect=explode):
            with self.assertRaises(RuntimeError):
                self.execute(preview)
        self.assertFalse(self.jobs().exists())
        self.assertEqual(IdempotencyRecord.objects.count(), records_before)
        for row in Announcement.objects.all():
            self.assertEqual(row.status, "draft")
            self.assertIsNone(row.approved_revision)

    def test_token_from_another_cohort_is_rejected(self):
        self.generated()
        preview = self.preview()
        other_course = Course.objects.create(slug="other-course", title="Other")
        other_cohort = Cohort.objects.create(
            course=other_course, slug="other-2027", title="Other 2027"
        )
        other_preview = self.preview(cohort=other_cohort)
        with self.assertRaises(LaunchError) as raised:
            self.execute(preview, preview_token=other_preview["preview_token"])
        self.assertEqual(raised.exception.status, 409)
        self.assertFalse(self.jobs().exists())
        self.assertEqual(Announcement.objects.get().status, "draft")

    def test_prospective_created_then_approved_skips_stale(self):
        first = self.published()
        second = self.published()
        assignment = AnnouncementAssignment.objects.create(course=self.course)
        assignment.versions.add(first)
        self.command("launch", cohort_id=str(self.cohort.pk))
        row = Announcement.objects.get()
        self.command("cancel", id=str(row.pk), expected_revision=row.revision)
        assignment.versions.add(second)
        preview = self.preview()
        prospective = [item for item in preview["rows"] if item["status"] == "prospective"]
        self.assertEqual(len(prospective), 1)
        self.command("launch", cohort_id=str(self.cohort.pk))
        created = Announcement.objects.get(generated_key=prospective[0]["generated_key"])
        self.command("approve", id=str(created.pk), expected_revision=created.revision)
        result = self.execute(preview)
        self.assertEqual(result["enqueued_count"], 0)
        skipped = {item["generated_key"]: item["reason"] for item in result["skipped"]}
        self.assertEqual(skipped[prospective[0]["generated_key"]], "stale_revision")
        self.assertEqual(self.jobs().count(), 0)

    def test_prospective_created_then_edited_skips_stale(self):
        first = self.published()
        second = self.published()
        assignment = AnnouncementAssignment.objects.create(course=self.course)
        assignment.versions.add(first)
        self.command("launch", cohort_id=str(self.cohort.pk))
        row = Announcement.objects.get()
        self.command("cancel", id=str(row.pk), expected_revision=row.revision)
        assignment.versions.add(second)
        preview = self.preview()
        prospective = [item for item in preview["rows"] if item["status"] == "prospective"]
        self.assertEqual(len(prospective), 1)
        # Another operator's generate creates the row, then edits its copy.
        self.command("launch", cohort_id=str(self.cohort.pk))
        created = Announcement.objects.get(generated_key=prospective[0]["generated_key"])
        self.command(
            "announcement_edit",
            id=str(created.pk),
            subject="Unseen edit",
            body="Unseen body",
            expected_revision=created.revision,
        )
        result = self.execute(preview)
        self.assertEqual(result["enqueued_count"], 0)
        skipped = {item["generated_key"]: item["reason"] for item in result["skipped"]}
        self.assertEqual(skipped[prospective[0]["generated_key"]], "stale_revision")
        created.refresh_from_db()
        self.assertEqual(created.status, "draft")
        self.assertIsNone(created.approved_revision)
        self.assertEqual(created.subject, "Unseen edit")
        self.assertEqual(self.jobs().count(), 0)

    def test_unknown_cohort_id_is_not_found(self):
        self.generated()
        preview = self.preview()
        with self.assertRaises(LaunchError) as raised:
            self.command(
                "launch_and_schedule",
                send=True,
                cohort_id="not-a-cohort",
                confirmed=True,
                preview_token=preview["preview_token"],
            )
        self.assertEqual(raised.exception.status, 404)
        self.assertFalse(self.jobs().exists())

    def test_prospective_created_then_context_edited_skips_stale(self):
        first = self.published()
        second = self.published()
        assignment = AnnouncementAssignment.objects.create(course=self.course)
        assignment.versions.add(first)
        self.command("launch", cohort_id=str(self.cohort.pk))
        row = Announcement.objects.get()
        self.command("cancel", id=str(row.pk), expected_revision=row.revision)
        assignment.versions.add(second)
        preview = self.preview()
        prospective = [item for item in preview["rows"] if item["status"] == "prospective"]
        self.assertEqual(len(prospective), 1)
        self.command("launch", cohort_id=str(self.cohort.pk))
        created = Announcement.objects.get(generated_key=prospective[0]["generated_key"])
        # Same subject/body as the version, but unseen context overrides.
        self.command(
            "announcement_edit",
            id=str(created.pk),
            subject=created.subject,
            body=created.body,
            context={"module_intro_paragraph": "Unseen intro"},
            expected_revision=created.revision,
        )
        result = self.execute(preview)
        self.assertEqual(result["enqueued_count"], 0)
        skipped = {item["generated_key"]: item["reason"] for item in result["skipped"]}
        self.assertEqual(skipped[prospective[0]["generated_key"]], "stale_revision")
        created.refresh_from_db()
        self.assertEqual(created.status, "draft")
        self.assertIsNone(created.approved_revision)
        self.assertEqual(self.jobs().count(), 0)

    def test_missing_confirmation_is_denied_before_locks(self):
        self.generated()
        preview = self.preview()
        with self.assertRaises(LaunchError):
            self.command(
                "launch_and_schedule",
                send=True,
                cohort_id=str(self.cohort.pk),
                preview_token=preview["preview_token"],
            )
        self.assertFalse(self.jobs().exists())

    def test_self_paced_preview_and_execute_fail_closed(self):
        self.generated()
        self.cohort.delivery_mode = "self_paced"
        self.cohort.save()
        with self.assertRaises(LaunchError):
            self.preview()
        with self.assertRaises(LaunchError):
            self.execute(
                {"cohort_id": str(self.cohort.pk), "preview_token": "bogus-token"},
                preview_token="bogus-token",
            )
        self.assertFalse(self.jobs().exists())

    def test_simulation_creates_no_live_transport_calls(self):
        version = self.published(channel="telegram")
        assignment = AnnouncementAssignment.objects.create(
            course=self.course, telegram_destination_key="primary"
        )
        assignment.versions.add(version)
        preview = self.preview()
        with patch(
            "courses.services.launch_transport.requests.post",
            side_effect=AssertionError("no transport during schedule"),
        ):
            result = self.execute(preview)
        self.assertEqual(result["enqueued_count"], 1)
        row = Announcement.objects.get(channel="telegram")
        row.refresh_from_db()
        self.assertEqual(row.status, "scheduled")
        self.assertFalse(AnnouncementDelivery.objects.filter(announcement=row).exists())


class BatchSharedPlacementTests(LaunchFixture, TestCase):
    """The real shared-curriculum key exceeds the idempotency key cap."""

    def make_shared_cohort(self):
        course = Course.objects.create(slug="shared-course", title="Shared course")
        shared = SharedCurriculum.objects.create(
            course=course,
            parser_version="course-repository-v2",
            **provenance("11111111-1111-4111-8111-111111111111", "course.yaml"),
        )
        module = SharedModule.objects.create(
            curriculum=shared,
            position=0,
            slug="01-agentic-rag",
            title="Agentic RAG",
            **provenance("22222222-2222-4222-8222-222222222222", "01-agentic-rag/module.yaml"),
        )
        cohort = Cohort.objects.create(
            course=course,
            slug="shared-2027",
            identifier="shared-2027",
            title="Shared 2027",
            curriculum_format="shared",
            shared_curriculum=shared,
        )
        placement = CohortSharedModule.objects.create(
            cohort=cohort,
            shared_module=module,
            position=0,
            starts_at=self.start,
        )
        return cohort, placement

    def preview(self, cohort):
        return read_launch({"resource": "batch_preview", "cohort_id": str(cohort.pk)})

    def shared_preview(self):
        version = self.published()
        cohort, _placement = self.make_shared_cohort()
        assignment = AnnouncementAssignment.objects.create(course=cohort.course)
        assignment.versions.add(version)
        preview = self.preview(cohort)
        self.assertEqual(preview["eligible_count"], 1)
        key = preview["rows"][0]["generated_key"]
        self.assertIn("shared_module_placement", key)
        # Keys travel as list VALUES (never JSON object keys), so no
        # 128-char object-key cap can ever reject a long shared key.
        self.assertLessEqual(len(key), 128)
        return preview, key

    def execute_preview(self, preview, key=None):
        import uuid

        return self.command(
            "launch_and_schedule",
            send=True,
            key=key or str(uuid.uuid4()),
            cohort_id=preview["cohort_id"],
            confirmed=True,
            preview_token=preview["preview_token"],
        )

    def jobs(self):
        return JobIntent.objects.filter(handler="courses.announcement")

    def test_shared_key_schedules_and_replays_with_real_key(self):
        preview, key = self.shared_preview()
        result = self.execute_preview(preview, key="shared-batch-key")
        self.assertEqual(result["enqueued_count"], 1)
        self.assertEqual(result["enqueued"][0]["generated_key"], key)
        self.assertEqual(self.jobs().count(), 1)
        replayed = self.execute_preview(preview, key="shared-batch-key")
        self.assertTrue(replayed["replayed"])
        self.assertEqual(replayed["enqueued_count"], 1)
        self.assertEqual(replayed["enqueued"][0]["generated_key"], key)
        self.assertEqual(self.jobs().count(), 1)

    def test_shared_key_end_to_end_over_api(self):
        preview, key = self.shared_preview()
        owner = make_studio_user(username="shared-api-owner", roles=("site_admin",))
        principal = APIPrincipal.objects.create(
            kind="human",
            name="shared-batch",
            identity_snapshot="user:shared-batch",
            user=owner,
        )
        principal.permissions.set(
            Permission.objects.filter(
                content_type__app_label="courses",
                codename__in=["manage_course_launch", "send_course_launch"],
            )
        )
        token = generate_token()
        raw = token.raw
        APICredential.objects.create(
            principal=principal,
            name="shared-batch",
            prefix=token.prefix,
            secret_digest=encode_secret(token.secret),
            digest_algorithm="pbkdf2_sha256",
            digest_version=1,
            scopes=["courses.launch.send"],
            expires_at=timezone.now() + timedelta(days=1),
        )
        payload = {
            "action": "launch_and_schedule",
            "cohort_id": preview["cohort_id"],
            "confirmed": True,
            "preview_token": preview["preview_token"],
        }
        headers = {
            "Authorization": f"Bearer {raw}",
            "Idempotency-Key": "shared-batch-api-key",
        }
        first = self.client.post(
            "/api/v1/admin/course-launch/dispatch",
            json.dumps(payload),
            content_type="application/json",
            headers=headers,
        )
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(first.json()["enqueued_count"], 1)
        self.assertEqual(first.json()["enqueued"][0]["generated_key"], key)
        second = self.client.post(
            "/api/v1/admin/course-launch/dispatch",
            json.dumps(payload),
            content_type="application/json",
            headers=headers,
        )
        self.assertEqual(second.status_code, 200, second.content)
        self.assertTrue(second.json()["replayed"])
        self.assertEqual(self.jobs().count(), 1)

    def test_shared_token_passes_through_cli_unchanged(self):
        from unittest.mock import patch

        from scripts.course_launch import main

        preview, _key = self.shared_preview()
        with (
            patch("scripts.course_launch.request", return_value={"enqueued_count": 1}) as send,
            patch("sys.stdout"),
        ):
            result = main(
                [
                    "batches",
                    "schedule",
                    "--cohort-id",
                    preview["cohort_id"],
                    "--confirmed",
                    "--preview-token",
                    preview["preview_token"],
                    "--json",
                ]
            )
        self.assertEqual(result, 0)
        self.assertEqual(send.call_args.args[1], "/api/v1/admin/course-launch/dispatch")
        payload = send.call_args.kwargs["payload"]
        self.assertEqual(payload["action"], "launch_and_schedule")
        self.assertEqual(payload["preview_token"], preview["preview_token"])
        self.assertIsNone(send.call_args.kwargs["revision"])


class BatchConcurrencyTests(LaunchFixture, TransactionTestCase):
    """A confirm racing an assignment save completes without deadlock or dupes."""

    def test_confirm_vs_assignment_save_completes_without_deadlock(self):
        self.generated()
        preview = read_launch(
            {"resource": "batch_preview", "cohort_id": str(self.cohort.pk)},
        )
        extra = self.published()
        assignment = AnnouncementAssignment.objects.get(course=self.course)
        barrier = Barrier(2)

        def attempt(callback):
            """SQLite serializes writers: retry a busy lock, then report."""
            import time

            error = None
            for _ in range(3):
                try:
                    return callback()
                except OperationalError as locked:
                    error = locked
                    time.sleep(0.05)
            return error

        def confirm():
            try:
                return self.command(
                    "launch_and_schedule",
                    send=True,
                    key="batch-race-confirm",
                    cohort_id=str(self.cohort.pk),
                    confirmed=True,
                    preview_token=preview["preview_token"],
                )
            except LaunchError as error:
                return error
            finally:
                connections["default"].close()

        def resave():
            try:
                return self.command(
                    "assignment_save",
                    course_id=str(self.course.pk),
                    version_ids=[
                        str(version.pk) for version in [*assignment.versions.all(), extra]
                    ],
                    expected_revision=assignment.revision,
                )
            except LaunchError as error:
                return error
            finally:
                connections["default"].close()

        def raced(callback, _position):
            close_old_connections()
            barrier.wait(timeout=10)
            return attempt(callback)

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(raced, confirm, 0)
            second = pool.submit(raced, resave, 1)
            results = [first.result(timeout=60), second.result(timeout=60)]
        self.assertTrue(
            all(isinstance(item, (dict, LaunchError)) for item in results),
            results,
        )
        jobs = JobIntent.objects.filter(handler="courses.announcement").count()
        self.assertLessEqual(jobs, 1)


class BatchPermissionTests(TestCase):
    def setUp(self):
        self.course = Course.objects.create(slug="perm-course", title="Perm course")
        self.cohort = Cohort.objects.create(course=self.course, slug="perm-2027", title="Perm 2027")

    def studio_post(self, username, roles, payload):
        user = make_studio_user(username=username, roles=roles)
        return authenticated_studio_client(user).post(
            "/studio/course-launch/dispatch/", payload, follow=True
        )

    def test_course_only_and_email_only_principals_are_denied(self):
        for roles in (("course_operator",), ("email_operator",)):
            response = self.studio_post(
                f"batch-denied-{roles[0]}",
                roles,
                {
                    "action": "launch_and_schedule",
                    "cohort_id": str(self.cohort.pk),
                    "confirmed": "true",
                    "preview_token": "bogus",
                    "idempotency_key": f"batch-denied-{roles[0]}",
                },
            )
            self.assertEqual(response.status_code, 403, roles)
        self.assertFalse(JobIntent.objects.filter(handler="courses.announcement").exists())
        self.assertFalse(Announcement.objects.exists())

    def test_api_send_only_principal_is_denied(self):
        owner = make_studio_user(username="batch-api-owner", roles=("site_admin",))
        principal = APIPrincipal.objects.create(
            kind="human",
            name="batch-send-only",
            identity_snapshot="user:batch-send-only",
            user=owner,
        )
        principal.permissions.set(
            Permission.objects.filter(
                content_type__app_label="courses", codename__in=["send_course_launch"]
            )
        )
        token = generate_token()
        raw = token.raw
        APICredential.objects.create(
            principal=principal,
            name="batch-send-only",
            prefix=token.prefix,
            secret_digest=encode_secret(token.secret),
            digest_algorithm="pbkdf2_sha256",
            digest_version=1,
            scopes=["courses.launch.send"],
            expires_at=timezone.now() + timedelta(days=1),
        )
        response = self.client.post(
            "/api/v1/admin/course-launch/dispatch",
            json.dumps(
                {
                    "action": "launch_and_schedule",
                    "cohort_id": str(self.cohort.pk),
                    "confirmed": True,
                    "preview_token": "bogus",
                }
            ),
            content_type="application/json",
            headers={
                "Authorization": f"Bearer {raw}",
                "Idempotency-Key": "batch-api-denied",
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(JobIntent.objects.filter(handler="courses.announcement").exists())


class BatchStudioPositiveTests(LaunchFixture, TestCase):
    def test_studio_preview_renders_and_confirm_schedules(self):
        self.generated()
        user = make_studio_user(username="batch-operator", roles=("site_admin",))
        studio = authenticated_studio_client(user)
        preview = studio.get(
            "/studio/course-launch/",
            {"resource": "batch_preview", "cohort_id": str(self.cohort.pk)},
        )
        self.assertEqual(preview.status_code, 200, preview.content[:500])
        self.assertContains(preview, "Batch preview")
        self.assertContains(preview, "Fingerprint:")
        from courses.services.launch_batch import build_preview

        built = build_preview(self.cohort)
        response = studio.post(
            "/studio/course-launch/dispatch/",
            {
                "action": "launch_and_schedule",
                "cohort_id": str(self.cohort.pk),
                "confirmed": "true",
                "preview_token": built["preview_token"],
                "idempotency_key": "batch-studio-confirm",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Scheduled 1")
        self.assertEqual(JobIntent.objects.filter(handler="courses.announcement").count(), 1)


class BatchCliTests(TestCase):
    def test_batches_preview_builds_get_contract(self):
        from unittest.mock import patch

        from scripts.course_launch import main

        with (
            patch("scripts.course_launch.request", return_value={"rows": []}) as send,
            patch("sys.stdout"),
        ):
            result = main(["batches", "preview", "--cohort-id", "synthetic", "--json"])
        self.assertEqual(result, 0)
        self.assertIn(
            "resource=batch_preview",
            send.call_args.args[1],
        )
        self.assertIn("cohort_id=synthetic", send.call_args.args[1])

    def test_batches_schedule_posts_dispatch_with_confirmation(self):
        from unittest.mock import patch

        from scripts.course_launch import main

        with (
            patch("scripts.course_launch.request", return_value={"enqueued_count": 1}) as send,
            patch("sys.stdout"),
        ):
            result = main(
                [
                    "batches",
                    "schedule",
                    "--cohort-id",
                    "synthetic",
                    "--confirmed",
                    "--preview-token",
                    "opaque-token",
                    "--json",
                ]
            )
        self.assertEqual(result, 0)
        self.assertEqual(send.call_args.args[1], "/api/v1/admin/course-launch/dispatch")
        payload = send.call_args.kwargs["payload"]
        self.assertEqual(payload["action"], "launch_and_schedule")
        self.assertEqual(payload["preview_token"], "opaque-token")
        self.assertIsNone(send.call_args.kwargs["revision"])
