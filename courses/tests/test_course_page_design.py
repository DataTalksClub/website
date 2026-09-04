"""Contracts for the design system course page (issue #179, mockup 6b).

The page's *arrangement* is the one it carried before the design system port and the
one the reference course page still has: a left-aligned hero, one row of every
action the course offers, then a Homework table and a Projects table whose
deadlines and states are all readable at once.  Mockup 6b's centred hero and
single module accordion are not part of that contract; its colours, pills, mono
labels and panels are.
"""

import re

from django.urls import reverse
from django.utils import timezone

from courses.course_page_content import (
    course_modules,
    course_specs,
    submission_progress,
)
from courses.models import HomeworkState, ProjectState
from courses.tests.course_view_base import CourseDetailViewTestBase, credentials


class CoursePageEditorialContentTests(CourseDetailViewTestBase):
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
        self.assertEqual(
            [spec.classes for spec in specs],
            ["spec-date", "spec-date", "", "", "", ""],
        )

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
    def test_course_page_does_not_render_backend_promise_copy(self):
        body = self.client.get(self.course_url()).content.decode()

        self.assertNotIn("you'll ship:", body)

    def test_course_page_does_not_render_a_cohort_year_eyebrow(self):
        body = self.client.get(self.course_url()).content.decode()
        hero_match = re.search(r"<section[^>]*\bcourse-hero\b[^>]*>", body)
        self.assertIsNotNone(hero_match)
        assert hero_match is not None
        hero_start = hero_match.start()
        hero = body[hero_start : body.index("</section>", hero_start)]

        self.assertNotIn("mono-label-indigo", hero)

    def test_course_page_carries_its_own_stylesheet_and_loads_no_legacy_css(self):
        """Design system (issue #179) replaced the adopted shell with one inline stylesheet."""

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

    def test_homework_and_projects_are_two_tables_of_open_rows(self):
        """The arrangement the page carried before the port, restored.

        Homework and projects are separate sections again, each a dashed row
        list with a caption row, and every deadline and state is on the page at
        once instead of behind a disclosure.
        """

        response = self.client.get(self.course_url())
        body = response.content.decode()
        page = body[body.index("<main") : body.index("</main>")]

        homework_count = len(response.context["homeworks"])
        project_count = len(response.context["projects"])
        self.assertContains(response, 'id="homework-heading"')
        self.assertContains(response, 'id="projects-heading"')
        self.assertContains(response, 'class="row-list course-rows"', count=2)
        self.assertContains(response, 'class="list-row course-rows-head"', count=2)
        self.assertContains(
            response,
            'class="list-row"',
            count=homework_count + project_count,
        )
        # Nothing on the page has to be opened to read a deadline or a state.
        self.assertNotIn("<details", page)

    def test_a_course_row_carries_its_deadline_beside_its_title(self):
        homework = self.homeworks[-1]

        response = self.client.get(self.course_url())
        body = response.content.decode()

        row = re.search(
            rf'<div class="list-row">(?:(?!</div>\s*</div>).)*?{re.escape(homework.title)}.*?'
            r'<div class="course-row-state">.*?</div>',
            body,
            re.DOTALL,
        )
        self.assertIsNotNone(row)
        self.assertIn('class="course-row-when"', row.group(0))
        self.assertIn('class="time-left"', row.group(0))
        self.assertIn('class="status-pill', row.group(0))

    def test_every_band_holds_the_shared_reading_column(self):
        """The course page holds the column the rest of the site holds."""

        body = self.client.get(self.course_url()).content.decode()
        page = body[body.index("<main") : body.index("</main>")]

        shells = re.findall(r'class="(shell[^"]*)"', page)
        self.assertTrue(shells)
        for shell in shells:
            with self.subTest(shell=shell):
                self.assertIn("content-shell", shell)

    def test_the_course_profile_is_an_action_beside_the_others(self):
        """Every control the course offers is in the one row of actions."""

        self.client.login(**credentials)

        body = self.client.get(self.course_url()).content.decode()

        profile_url = reverse(
            "enrollment",
            kwargs={
                "course_slug": self.course.course.slug,
                "cohort_year": self.course.year,
            },
        )
        self.assertLess(body.index('class="course-actions"'), body.index(profile_url))
        self.assertLess(body.index(profile_url), body.index("Your work in this course"))

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
                        "course_slug": self.course.course.slug,
                        "cohort_year": self.course.year,
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
                    "course_slug": self.course.course.slug,
                    "cohort_year": self.course.year,
                    "project_slug": self.completed_project.slug,
                },
            ),
        )

    def test_the_faq_panel_appears_only_for_a_course_that_has_one(self):
        response = self.client.get(self.course_url())
        self.assertNotContains(response, "Questions before you start?")

        self.course.faq_document_url = "https://example.invalid/course-faq"
        self.course.save(update_fields=["faq_document_url"])

        response = self.client.get(self.course_url())

        self.assertContains(response, "Questions before you start?")
        self.assertContains(response, 'href="https://example.invalid/course-faq"')

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


class CoursePageBreadcrumbTests(CourseDetailViewTestBase):
    """The trail the adopted shell drew, restored as the design system primitive."""

    def breadcrumb_nav(self):
        body = self.client.get(self.course_url()).content.decode()
        trails = re.findall(
            r'<nav class="[^"]*\bbreadcrumbs\b[^"]*" aria-label="Breadcrumb">(.*?)</nav>',
            body,
            re.DOTALL,
        )
        self.assertEqual(len(trails), 1)
        return body, trails[0]

    def test_the_trail_is_an_ordered_list_of_the_ancestors_of_this_course(self):
        _body, trail = self.breadcrumb_nav()

        self.assertIn("<ol>", trail)
        self.assertNotIn("<ul>", trail)
        crumbs = re.findall(r"<li[^>]*>(.*?)</li>", trail, re.DOTALL)
        self.assertEqual(len(crumbs), 2)
        self.assertIn(f'href="{reverse("course_list")}"', crumbs[0])
        self.assertIn(">Courses<", crumbs[0])
        self.assertIn(f">{self.course.title}<", crumbs[1])

    def test_the_trail_stops_before_this_cohort_because_the_heading_names_it(self):
        """A last crumb repeating the h1 is duplication, so the trail leaves it out."""

        body, trail = self.breadcrumb_nav()

        self.assertNotIn('aria-current="page"', trail)
        self.assertNotIn(str(self.course.year), trail)
        # The cohort is still named on the page — once, as the heading.
        self.assertIn(f'<h1 id="course-heading">{self.course.title}</h1>', body)

    def test_every_crumb_is_a_link_back_to_an_ancestor(self):
        _body, trail = self.breadcrumb_nav()

        crumbs = re.findall(r"<li[^>]*>(.*?)</li>", trail, re.DOTALL)
        self.assertTrue(crumbs)
        for crumb in crumbs:
            self.assertIn("<a", crumb)

    def test_separators_are_css_drawn_and_never_written_into_the_markup(self):
        body, trail = self.breadcrumb_nav()

        visible_text = re.sub(r"<[^>]+>", " ", trail)
        self.assertNotIn("/", visible_text)
        self.assertNotIn("aria-hidden", trail)
        # The shared stylesheet draws the separator with empty alternative
        # text, so assistive technology never announces it.
        self.assertIn('content: "/" / ""', body)
