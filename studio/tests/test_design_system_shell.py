"""The Studio surfaces carry the shared design-system shell.

Studio is ~30 near-identical staff pages, so unlike the public pages — which are
each a standalone design-system document — this surface keeps one base template per
section: ``templates/studio/base.html`` and the Studio Courses base that extends
it.  These tests pin what that base guarantees every page below it, whatever the
page itself renders: one inline stylesheet and no external CSS, the shared
masthead/footer/script partials rather than a copy of them, the noindex marker
the surface has always carried, and one h1 per page.

A page rebuilt on the base later must keep passing this; a page that forks the
shell, links a stylesheet or grows a second h1 fails here.
"""

from __future__ import annotations

import re
from datetime import timedelta

from django.templatetags.static import static
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import CustomUser
from accounts.studio_test_support import make_studio_user
from core.models import AuditEvent
from courses.models import (
    Enrollment,
    Homework,
    HomeworkState,
    Project,
    ProjectState,
    ProjectSubmission,
    Submission,
    User,
)
from courses.models.course import Course
from events.identity import create_event_identity
from events.models import Event

SHELL_PARTIALS = ("core/_site_shell_head.html", "core/_site_shell_foot.html")

# The shared script set, in the order core/_site_shell_foot.html loads it.  A
# Studio surface may append its own scripts after these, never before them.
EXPECTED_SCRIPTS = (
    "timezone_preference.js",
    "user_menu.js",
    "core/site_navigation.js",
    "core/accessibility.js",
    "core/analytics_preferences.js",
)

SCRIPT_SOURCE = re.compile(r'<script src="([^"]+)"')


class StudioDesignFiveAShellTests(TestCase):
    course: Course
    audit_event: AuditEvent
    event: Event
    student: CustomUser
    enrollment: Enrollment
    homework: Homework
    homework_submission: Submission
    project: Project
    project_submission: ProjectSubmission

    @classmethod
    def setUpTestData(cls) -> None:
        cls.course = Course.objects.create(
            slug="shell-contract-course",
            title="Shell Contract Course",
            description="Fixture for the Studio design 5a shell contract.",
        )
        cls.audit_event = AuditEvent.objects.create(
            actor_ref="shell-contract-actor",
            action="tests.shell.contract",
            target_type="tests.shell",
            target_label="shell contract fixture",
            outcome=AuditEvent.Outcome.SUCCEEDED,
            changes={},
            metadata={"summary": "Fixture for the Studio design 5a shell contract."},
        )
        cls.event = create_event_identity(
            title="Shell Contract Event",
            source_repository="DataTalksClub/shell-contract",
            source_revision="a" * 40,
            source_key="shell-contract-event",
        )
        # The grading surfaces need something to grade, so the fixture carries
        # one homework and one project with a submission apiece.
        cls.student = User.objects.create_user(
            username="shell-contract-student",
            email="shell-contract-student@example.com",
            password="shell-contract",
        )
        cls.enrollment = Enrollment.objects.create(
            student=cls.student,
            course=cls.course,
        )
        cls.homework = Homework.objects.create(
            course=cls.course,
            slug="shell-contract-homework",
            title="Shell Contract Homework",
            due_date=timezone.now() + timedelta(days=7),
            state=HomeworkState.OPEN.value,
        )
        cls.homework_submission = Submission.objects.create(
            homework=cls.homework,
            student=cls.student,
            enrollment=cls.enrollment,
        )
        cls.project = Project.objects.create(
            course=cls.course,
            slug="shell-contract-project",
            title="Shell Contract Project",
            submission_due_date=timezone.now() + timedelta(days=7),
            peer_review_due_date=timezone.now() + timedelta(days=14),
            state=ProjectState.COLLECTING_SUBMISSIONS.value,
        )
        cls.project_submission = ProjectSubmission.objects.create(
            project=cls.project,
            student=cls.student,
            enrollment=cls.enrollment,
            github_link="https://github.com/DataTalksClub/shell-contract",
            commit_id="abc123",
        )

    def studio_paths(self) -> dict[str, str]:
        return {
            "studio home": reverse("studio:home"),
            "site settings": reverse("studio:settings"),
            "site navigation": reverse("studio:navigation"),
            "audit events": reverse("studio:audit-list"),
            "audit event detail": reverse(
                "studio:audit-detail",
                kwargs={"event_id": self.audit_event.id},
            ),
            "event identities": reverse("studio:event-identity-list"),
            "event identity detail": reverse(
                "studio:event-identity-detail",
                kwargs={"event_id": self.event.id},
            ),
            "historical registration totals": reverse("studio:historical-registration-list"),
            "historical event mappings": reverse("studio:historical-registration-mappings"),
            "course registration totals": reverse("studio:course-registration-count-list"),
        }

    def studio_courses_paths(self) -> dict[str, str]:
        return {
            "courses list": reverse("studio_courses_course_list"),
            "course workspace": reverse(
                "studio_courses_course",
                kwargs={"course_slug": self.course.slug},
            ),
            "course enrollments": reverse(
                "studio_courses_enrollments",
                kwargs={"course_slug": self.course.slug},
            ),
            "leaderboard flags": reverse(
                "studio_courses_leaderboard_complaints",
                kwargs={"course_slug": self.course.slug},
            ),
            "enrollment record": reverse(
                "studio_courses_enrollment_edit",
                kwargs={
                    "course_slug": self.course.slug,
                    "enrollment_id": self.enrollment.id,
                },
            ),
            "landing page builder": reverse("studio_courses_campaign_create"),
            "homework submissions": reverse(
                "studio_courses_homework_submissions",
                kwargs={
                    "course_slug": self.course.slug,
                    "homework_slug": self.homework.slug,
                },
            ),
            "homework submission edit": reverse(
                "studio_courses_homework_submission_edit",
                kwargs={
                    "course_slug": self.course.slug,
                    "homework_slug": self.homework.slug,
                    "submission_id": self.homework_submission.id,
                },
            ),
            "project submissions": reverse(
                "studio_courses_project_submissions",
                kwargs={
                    "course_slug": self.course.slug,
                    "project_slug": self.project.slug,
                },
            ),
            "project submission edit": reverse(
                "studio_courses_project_submission_edit",
                kwargs={
                    "course_slug": self.course.slug,
                    "project_slug": self.project.slug,
                    "submission_id": self.project_submission.id,
                },
            ),
            "datamailer operations": reverse("studio_courses_datamailer_operations"),
            "datamailer events": reverse("studio_courses_datamailer_events"),
        }

    def rendered_pages(self) -> dict[str, str]:
        bodies: dict[str, str] = {}

        studio_user = make_studio_user(username="shell-site-admin", roles=("site_admin",))
        self.client.force_login(studio_user)
        for name, path in self.studio_paths().items():
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, name)
            for partial in SHELL_PARTIALS:
                self.assertTemplateUsed(response, partial, msg_prefix=name)
            bodies[name] = response.content.decode()

        # Studio Courses keeps the copied platform's own staff check, so its
        # pages are read here as the operator that check expects.
        operator = make_studio_user(username="shell-course-operator", roles=("course_operator",))
        operator.is_superuser = True
        operator.save(update_fields=["is_superuser"])
        self.client.force_login(operator)
        for name, path in self.studio_courses_paths().items():
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, name)
            for partial in SHELL_PARTIALS:
                self.assertTemplateUsed(response, partial, msg_prefix=name)
            bodies[name] = response.content.decode()

        return bodies

    def test_every_studio_page_carries_one_inline_stylesheet_and_no_external_css(self) -> None:
        for name, body in self.rendered_pages().items():
            with self.subTest(page=name):
                self.assertEqual(body.count("<style>"), 1)
                self.assertNotIn('<link rel="stylesheet"', body)
                # The design system partial is what that one style element holds.
                self.assertIn("--lavender-deep:", body)

    def test_every_studio_page_stays_out_of_the_index(self) -> None:
        for name, body in self.rendered_pages().items():
            with self.subTest(page=name):
                self.assertIn('<meta name="robots" content="noindex,nofollow">', body)

    def test_every_studio_page_loads_the_shared_script_set_first(self) -> None:
        expected = [static(source) for source in EXPECTED_SCRIPTS]

        for name, body in self.rendered_pages().items():
            with self.subTest(page=name):
                sources = SCRIPT_SOURCE.findall(body)
                self.assertEqual(sources[: len(expected)], expected)

    def test_every_studio_page_has_exactly_one_top_level_heading(self) -> None:
        for name, body in self.rendered_pages().items():
            with self.subTest(page=name):
                self.assertEqual(body.count("<h1"), 1)

    def test_studio_courses_pages_carry_the_trail_back_to_studio(self) -> None:
        bodies = self.rendered_pages()

        for name in self.studio_courses_paths():
            with self.subTest(page=name):
                body = bodies[name]
                self.assertIn('<nav class="breadcrumbs" aria-label="Breadcrumb">', body)
                self.assertIn(f'<a href="{reverse("studio:home")}">Studio</a>', body)

        # The base's own trail is the courses list's, and the design system's
        # breadcrumb names the current page instead of linking to itself.
        self.assertIn('<li aria-current="page">', bodies["courses list"])
