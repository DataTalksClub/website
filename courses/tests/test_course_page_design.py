"""Contracts for the design 5a course page (issue #179, mockup 6b)."""

import re

from django.urls import reverse
from django.utils import timezone

from core.home_content import COURSE_FAMILIES
from courses.course_page_content import (
    COURSE_SHIP_LINES,
    course_modules,
    course_ship_line,
    course_specs,
    submission_progress,
)
from courses.models import HomeworkState, ProjectState
from courses.tests.course_view_base import CourseDetailViewTestBase, credentials


class CoursePageEditorialContentTests(CourseDetailViewTestBase):
    def test_ship_lines_repeat_the_homepage_promise_for_the_same_family(self):
        homepage_promises = {family: promise for family, _t, _l, promise in COURSE_FAMILIES}

        self.assertEqual(dict(COURSE_SHIP_LINES), homepage_promises)

    def test_an_unknown_course_family_gets_no_invented_promise(self):
        self.assertEqual(course_ship_line("test-course-2"), "")
        self.assertEqual(
            course_ship_line("ml-zoomcamp-2026"),
            "trained models your classmates review",
        )

    def test_specs_carry_only_the_facts_the_course_record_has(self):
        self.course.start_date = timezone.datetime(2026, 9, 14).date()
        self.course.end_date = None
        self.course.home_duration_label = "TBA"

        specs = course_specs(
            self.course,
            homework_count=3,
            project_count=0,
            signup_count=None,
        )

        self.assertEqual(
            [(spec.label, spec.value) for spec in specs],
            [("starts", "Mon, Sep 14, 2026"), ("homework", "3")],
        )

    def test_specs_include_every_fact_that_is_present(self):
        self.course.start_date = timezone.datetime(2026, 9, 14).date()
        self.course.end_date = timezone.datetime(2027, 1, 25).date()
        self.course.home_duration_label = "19 weeks"

        specs = course_specs(
            self.course,
            homework_count=9,
            project_count=2,
            signup_count=643,
        )

        self.assertEqual(
            [spec.label for spec in specs],
            ["starts", "ends", "length", "homework", "projects", "registered"],
        )
        self.assertEqual(specs[-1].value, "643 people")

    def test_modules_number_homework_then_projects_continuously(self):
        modules = course_modules(["a", "b"], ["c"])

        self.assertEqual([module.number for module in modules], ["01", "02", "03"])
        self.assertEqual([module.kind for module in modules], ["homework", "homework", "project"])

    def test_progress_counts_only_the_modules_this_learner_submitted(self):
        class Item:
            def __init__(self, submitted):
                self.submitted = submitted

        modules = course_modules([Item(True), Item(False)], [Item(True), Item(False)])

        progress = submission_progress(modules)

        self.assertEqual(progress.submitted, 2)
        self.assertEqual(progress.total, 4)
        self.assertEqual(progress.percent, 50)

    def test_progress_is_absent_when_the_course_has_no_modules(self):
        self.assertIsNone(submission_progress(()))


class CoursePageRenderTests(CourseDetailViewTestBase):
    def test_course_page_carries_its_own_stylesheet_and_loads_no_legacy_css(self):
        """Design 5a (issue #179) replaced the adopted shell with one inline stylesheet."""

        body = self.client.get(self.course_url()).content.decode()

        self.assertIn("<style>", body)
        for retired in (
            "/static/courses.css",
            "/static/core/site_shell.css",
            "/static/core/accessibility.css",
            "tailwindcss",
            "fontawesome",
        ):
            with self.subTest(asset=retired):
                self.assertNotIn(retired, body)
        self.assertEqual(re.findall(r'<link[^>]+rel="stylesheet"', body), [])

    def test_course_page_leaks_no_unrendered_template_syntax(self):
        body = self.client.get(self.course_url()).content.decode()

        for leak in ("{#", "#}", "{%", "%}", "{{", "}}"):
            with self.subTest(token=leak):
                self.assertNotIn(leak, body)

    def test_every_homework_and_project_becomes_a_numbered_module(self):
        response = self.client.get(self.course_url())

        modules = response.context["course_modules"]
        self.assertEqual(
            len(modules),
            len(response.context["homeworks"]) + len(response.context["projects"]),
        )
        self.assertContains(response, 'class="module-list"')
        self.assertContains(response, "<details class=\"module\" open>", count=1)
        self.assertContains(response, 'class="module-number"', count=len(modules))

    def test_a_signed_out_visitor_sees_no_learner_panel(self):
        response = self.client.get(self.course_url())

        self.assertNotContains(response, "Your work in this course")
        self.assertIsNone(response.context["submission_progress"])
        # The shared stylesheet documents the progressbar role in a comment, so the
        # absence of the bar is asserted against the element the page would draw.
        self.assertNotContains(response, 'class="progress-fill"')

    def test_an_enrolled_learner_sees_a_progress_bar_backed_by_real_submissions(self):
        self.client.login(**credentials)

        response = self.client.get(self.course_url())

        progress = response.context["submission_progress"]
        submitted = sum(1 for homework in response.context["homeworks"] if homework.submitted)
        submitted += sum(1 for project in response.context["projects"] if project.submitted)
        self.assertEqual(progress.submitted, submitted)
        self.assertContains(response, "Your work in this course")
        self.assertContains(response, 'class="progress-fill"')
        self.assertContains(
            response,
            f'aria-valuenow="{progress.submitted}"',
        )
        self.assertContains(
            response,
            f"{progress.submitted} of {progress.total} modules submitted",
        )

    def test_a_module_keeps_its_deadline_status_and_the_page_it_links_to(self):
        homework = self.homeworks[0]

        response = self.client.get(self.course_url())

        self.assertContains(response, homework.title)
        if homework.state != HomeworkState.CLOSED.value:
            self.assertContains(
                response,
                reverse(
                    "homework",
                    kwargs={
                        "course_slug": self.course.slug,
                        "homework_slug": homework.slug,
                    },
                ),
            )
        self.assertContains(response, 'class="status-pill')

    def test_a_project_module_keeps_its_state_specific_destination(self):
        self.completed_project.state = ProjectState.COLLECTING_SUBMISSIONS.value
        self.completed_project.save()

        response = self.client.get(self.course_url())

        self.assertContains(
            response,
            reverse(
                "project",
                kwargs={
                    "course_slug": self.course.slug,
                    "project_slug": self.completed_project.slug,
                },
            ),
        )

    def test_the_fact_strip_is_absent_when_the_course_has_no_facts(self):
        self.homeworks_queryset_cleanup()

        response = self.client.get(self.course_url())

        self.assertEqual(response.context["course_specs"], ())
        self.assertNotContains(response, 'class="spec-strip course-specs"')

    def homeworks_queryset_cleanup(self):
        self.course.homework_set.all().delete()
        self.course.project_set.all().delete()
        self.course.start_date = None
        self.course.end_date = None
        self.course.save()
