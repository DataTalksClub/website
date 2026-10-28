"""Launch behavior is tested at its shared service boundary; no network transports."""

import uuid
from datetime import timedelta

from community_base.kernel.services import ServiceContext
from django.utils import timezone

from courses.models import (
    Announcement,
    AnnouncementAssignment,
    AnnouncementTemplateVersion,
    Cohort,
    Course,
    Homework,
    Module,
    Project,
)
from courses.services.launch_commands import send_launch, write_launch


class LaunchFixture:
    def setUp(self):
        self.course = Course.objects.create(slug="launch-test", title="Launch test")
        self.cohort = Cohort.objects.create(
            course=self.course,
            slug="launch-2027",
            identifier="2027",
            title="Launch 2027",
            curriculum_format="modules",
        )
        self.start = timezone.now() + timedelta(days=7)
        self.homework = Homework.objects.create(
            course=self.cohort,
            slug="hw1",
            title="Homework",
            due_date=self.start + timedelta(days=7),
        )
        self.module = Module.objects.create(
            cohort=self.cohort,
            position=1,
            slug="one",
            title="First module",
            terminal_homework=self.homework,
            starts_at=self.start,
        )
        self.project = self.make_project()

    def make_project(self):
        return Project.objects.create(
            course=self.cohort,
            slug="project",
            title="Project",
            starts_at=self.start,
            submission_due_date=self.start + timedelta(days=14),
            peer_review_due_date=self.start + timedelta(days=21),
        )

    def command(self, action, *, send=False, key=None, **values):
        context = ServiceContext.from_current(
            actor_ref="user:1", idempotency_key=key or str(uuid.uuid4())
        )
        callback = write_launch
        if send:
            callback = send_launch
        return callback({"action": action, **values}, context=context)

    def published(self, channel="email", kind="module"):
        draft = self.command(
            "template_save",
            name="Start",
            channel=channel,
            kind=kind,
            subject="{{ module_title }} starts",
            body="Start {{ module_number }} {{ module_title }}: {{ module_url }}",
        )
        result = self.command("template_publish", id=draft["id"], expected_revision=1)
        return AnnouncementTemplateVersion.objects.get(pk=result["id"])

    def generated(self, channel="email"):
        version = self.published(channel)
        assignment = AnnouncementAssignment.objects.create(course=self.course)
        assignment.versions.add(version)
        self.command("launch", cohort_id=str(self.cohort.pk))
        return Announcement.objects.get()

    def approve(self, row):
        self.command("approve", id=str(row.pk), expected_revision=row.revision)
        row.refresh_from_db()

    def update_start(self, value):
        return self.command(
            "timing_update",
            id=str(self.module.pk),
            target_kind="module",
            starts_at=value,
            provenance="Reviewed start",
            expected_revision=0,
        )
