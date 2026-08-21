"""The learner project surfaces carry the design 5a shell (issue #179).

The project pages — the project itself, the two submission lists, the two
peer-evaluation surfaces, the results and the statistics — are the same shape.
The general project pages use ``courses/templates/projects/_base.html`` and the
project submission/review forms reuse ``courses/templates/courses/_submission_page.html``.
These tests pin what those shared documents guarantee every page below them:
one inline stylesheet and no external CSS, the shared masthead/footer/script
partials rather than a copy of them, the trail back to the course, and one h1
per page.

`projects/submissions.html` is the one project template not read here: its only
route (`project_submissions`) redirects every caller — to the project page
without the course-operator role, and to the Studio Courses table with it — so
no request renders it.  It is still carried, and still on the base, because the
adoption manifest pins the file.

A page rebuilt on the base later must keep passing this; a page that forks the
shell, links a stylesheet or grows a second h1 fails here.
"""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path

from django.templatetags.static import static
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.studio_test_support import make_studio_user
from courses.models import (
    Cohort,
    Enrollment,
    PeerReview,
    Project,
    ProjectState,
    ProjectSubmission,
    User,
)

PROJECT_TEMPLATES = Path(__file__).resolve().parents[1] / "templates/projects"
SHARED_SUBMISSION_PAGES = {"project.html", "eval_submit.html"}

SHELL_PARTIALS = ("core/_site_shell_head.html", "core/_site_shell_foot.html")

# The shared script set, in the order core/_site_shell_foot.html loads it.  A
# project page may append its own scripts after these, never before them.
EXPECTED_SCRIPTS = (
    "timezone_preference.js",
    "core/code_blocks.js",
    "user_menu.js",
    "core/site_navigation.js",
    "core/accessibility.js",
    "core/analytics_preferences.js",
)

SCRIPT_SOURCE = re.compile(r'<script src="([^"]+)"')

# Every project page except the project itself is a thin working surface that
# the copied platform has always kept out of the index.
INDEXED_PAGES = ("project",)
SHARED_SUBMISSION_PAGES = {"project.html", "eval_submit.html"}


def cohort_model():
    """Resolve the project relation without importing the legacy Course name."""

    return Project._meta.get_field("course").remote_field.model


class ProjectDesignFiveAShellTests(TestCase):
    def setUp(self) -> None:
        self.course = Cohort.objects.create(
            slug="shell-project-course",
            title="Shell Project Course",
            description="Fixture for the project design 5a shell contract.",
        )
        self.project = Project.objects.create(
            course=self.course,
            slug="shell-project",
            title="Shell Project",
            description="Build something and hand it in.",
            instructions_url="https://github.com/DataTalksClub/shell-project",
            submission_due_date=timezone.now() + timedelta(days=7),
            peer_review_due_date=timezone.now() + timedelta(days=14),
            state=ProjectState.PEER_REVIEWING.value,
            learning_in_public_cap_project=2,
            faq_contribution_field=True,
            time_spent_project_field=True,
        )
        self.learner = make_studio_user(
            username="shell-project-operator",
            roles=("course_operator",),
        )
        self.enrollment = Enrollment.objects.create(
            student=self.learner,
            course=self.course,
            display_name="Shell Learner",
        )
        self.submission = ProjectSubmission.objects.create(
            project=self.project,
            student=self.learner,
            enrollment=self.enrollment,
            github_link="https://github.com/DataTalksClub/shell-project-work",
            commit_id="abc1234",
        )
        self.review = self.create_peer_review()

    def create_peer_review(self) -> PeerReview:
        peer = User.objects.create_user(
            username="shell-project-peer",
            email="shell-project-peer@example.invalid",
            password="shell-project",
        )
        peer_enrollment = Enrollment.objects.create(
            student=peer,
            course=self.course,
            display_name="Shell Peer",
        )
        peer_submission = ProjectSubmission.objects.create(
            project=self.project,
            student=peer,
            enrollment=peer_enrollment,
            github_link="https://github.com/DataTalksClub/shell-project-peer",
            commit_id="def5678",
        )
        return PeerReview.objects.create(
            submission_under_evaluation=peer_submission,
            reviewer=self.submission,
            note_to_peer="",
        )

    def project_paths(self) -> dict[str, str]:
        route_kwargs = self.course_route_kwargs()
        project_slug = self.project.slug
        return {
            "project": reverse(
                "project",
                kwargs={**route_kwargs, "project_slug": project_slug},
            ),
            "project submissions list": reverse(
                "project_list",
                kwargs={**route_kwargs, "project_slug": project_slug},
            ),
            "all project submissions": reverse(
                "list_all_project_submissions",
                kwargs=route_kwargs,
            ),
            "peer evaluations": reverse(
                "projects_eval",
                kwargs={**route_kwargs, "project_slug": project_slug},
            ),
            "peer review form": reverse(
                "projects_eval_submit",
                kwargs={
                    **route_kwargs,
                    "project_slug": project_slug,
                    "review_id": self.review.id,
                },
            ),
            "project results": reverse(
                "project_results",
                kwargs={**route_kwargs, "project_slug": project_slug},
            ),
        }

    def course_route_kwargs(self) -> dict[str, object]:
        if self.course._meta.model_name == "cohort":
            return {
                "course_slug": self.course.course.slug,
                "cohort_year": self.course.year,
            }
        return {"course_slug": self.course.slug}

    def statistics_path(self) -> str:
        """The statistics page redirects until the project is scored."""
        self.project.state = ProjectState.COMPLETED.value
        self.project.save(update_fields=["state"])
        return reverse(
            "project_statistics",
            kwargs={
                **self.course_route_kwargs(),
                "project_slug": self.project.slug,
            },
        )

    def rendered_pages(self) -> dict[str, str]:
        bodies: dict[str, str] = {}
        self.client.force_login(self.learner)
        paths = self.project_paths()
        # The statistics page is the one surface that needs a scored project, so
        # it is read last, after every other page has been read in the state it
        # is normally met in.
        for name, path in paths.items():
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, name)
            for partial in SHELL_PARTIALS:
                self.assertTemplateUsed(response, partial, msg_prefix=name)
            bodies[name] = response.content.decode()

        response = self.client.get(self.statistics_path())
        self.assertEqual(response.status_code, 200, "project statistics")
        for partial in SHELL_PARTIALS:
            self.assertTemplateUsed(response, partial, msg_prefix="project statistics")
        bodies["project statistics"] = response.content.decode()
        return bodies

    def test_every_project_page_carries_one_inline_stylesheet_and_no_external_css(self) -> None:
        for name, body in self.rendered_pages().items():
            with self.subTest(page=name):
                self.assertEqual(body.count("<style>"), 1)
                self.assertNotIn('<link rel="stylesheet"', body)
                # The design system partial is what that one style element holds.
                self.assertIn("--lavender-deep:", body)

    def test_every_project_page_loads_the_shared_script_set_first(self) -> None:
        expected = [static(source) for source in EXPECTED_SCRIPTS]

        for name, body in self.rendered_pages().items():
            with self.subTest(page=name):
                sources = SCRIPT_SOURCE.findall(body)
                self.assertEqual(sources[: len(expected)], expected)

    def test_every_project_page_has_exactly_one_top_level_heading(self) -> None:
        for name, body in self.rendered_pages().items():
            with self.subTest(page=name):
                self.assertEqual(body.count("<h1"), 1)

    def test_every_project_page_carries_the_trail_back_to_the_course(self) -> None:
        course_url = reverse("course", kwargs=self.course_route_kwargs())

        for name, body in self.rendered_pages().items():
            with self.subTest(page=name):
                self.assertRegex(
                    body,
                    r'<nav class="(?:shell shell-reading )?breadcrumbs" aria-label="Breadcrumb">',
                )
                self.assertIn(f'<a href="{reverse("course_list")}">Courses</a>', body)
                self.assertIn(f'<a href="{course_url}">{self.course.title}</a>', body)
                self.assertIn('<li aria-current="page">', body)

    def test_only_the_project_page_itself_is_offered_to_the_index(self) -> None:
        noindex = '<meta name="robots" content="noindex">'

        for name, body in self.rendered_pages().items():
            with self.subTest(page=name):
                if name in INDEXED_PAGES:
                    self.assertNotIn(noindex, body)
                else:
                    self.assertIn(noindex, body)

    def test_no_project_page_keeps_its_own_copy_of_the_shell(self) -> None:
        """The shell is included by the base, never inlined by a page."""

        source = (PROJECT_TEMPLATES / "_base.html").read_text(encoding="utf-8")
        parent_source = (
            PROJECT_TEMPLATES.parents[2] / "templates/core/content_page.html"
        ).read_text(encoding="utf-8")
        self.assertIn('{% extends "core/content_page.html" %}', source)
        for partial in SHELL_PARTIALS:
            self.assertIn(f'{{% include "{partial}" %}}', parent_source)

        for template in sorted(PROJECT_TEMPLATES.glob("*.html")):
            with self.subTest(template=template.name):
                page = template.read_text(encoding="utf-8")
                self.assertNotIn('<header class="masthead">', page)
                self.assertNotIn("analytics_preferences.js", page)
                if template.name in SHARED_SUBMISSION_PAGES:
                    self.assertIn(
                        '{% extends "courses/_submission_page.html" %}',
                        page,
                    )
                elif template.name != "_base.html":
                    self.assertIn("{% extends 'projects/_base.html' %}", page)

        for name, body in self.rendered_pages().items():
            with self.subTest(page=name):
                self.assertEqual(body.count('id="site-navigation-links"'), 1)

    def test_project_and_peer_review_forms_inherit_and_render_cmp_primitives(self) -> None:
        project_template = (PROJECT_TEMPLATES / "project.html").read_text(encoding="utf-8")
        review_template = (PROJECT_TEMPLATES / "eval_submit.html").read_text(encoding="utf-8")
        shared_template = (PROJECT_TEMPLATES.parent / "courses/_submission_page.html").read_text(
            encoding="utf-8"
        )

        self.assertIn('{% extends "courses/_submission_page.html" %}', project_template)
        self.assertIn('{% extends "courses/_submission_page.html" %}', review_template)
        self.assertIn('{% extends "core/content_page.html" %}', shared_template)
        self.assertIn(
            '{% block content_band_class %}submission-band{% endblock %}',
            shared_template,
        )

        bodies = self.rendered_pages()
        self.assertIn('class="needs-validation cmp-form project-form', bodies["project"])
        self.assertIn('class="needs-validation cmp-form review-form', bodies["peer review form"])
        for name in ("project", "peer review form"):
            with self.subTest(page=name):
                self.assertIn('class="field learning-in-public-field"', bodies[name])
                self.assertIn(".cmp-form {", bodies[name])
                self.assertIn("background: var(--lavender);", bodies[name])
                self.assertIn("--form-measure: 46rem;", bodies[name])
                self.assertIn(".cmp-form-actions {", bodies[name])
