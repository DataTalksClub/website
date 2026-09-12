from pathlib import Path

from django.urls import reverse

from courses.tests.dashboard_view_base import DashboardViewTestBase


class DashboardViewTestCase(DashboardViewTestBase):
    def dashboard_url(self):
        return reverse(
            "cohort_dashboard",
            kwargs={
                "course_slug": self.course.course.slug,
                "cohort_identifier": self.course.identifier,
            },
        )

    def test_dashboard_url_exists(self):
        url = self.dashboard_url()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_dashboard_uses_correct_template(self):
        url = self.dashboard_url()
        response = self.client.get(url)
        self.assertTemplateUsed(response, "courses/dashboard.html")

    def test_dashboard_context_basic(self):
        url = self.dashboard_url()
        response = self.client.get(url)

        self.assertIn("course", response.context)
        self.assertIn("total_enrollments", response.context)
        self.assertIn("homework_stats", response.context)
        self.assertIn("homework_difficulty_stats", response.context)
        self.assertIn("project_passing_score", response.context)

        self.assertEqual(response.context["course"], self.course)
        self.assertEqual(response.context["total_enrollments"], 6)
        self.assertEqual(response.context["project_passing_score"], 70)

    def test_page_carries_one_documented_width_exception_instead_of_per_section_breakouts(self):
        # The redesign (issue #237) replaced the page's two alignment grids --
        # a 38rem prose measure with per-section `.shell-breakout` escapes to
        # 76rem -- with one documented width exception for the whole page, so
        # every section shares one edge instead of switching grids four
        # times. Wide tables inside a horizontal scroll frame are gone with
        # them: the homework and question-difficulty data now reshapes as a
        # row-list instead of scrolling sideways.
        body = (Path(__file__).resolve().parents[1] / "templates/courses/dashboard.html").read_text(
            encoding="utf-8"
        )

        self.assertIn(".content-shell {", body)
        self.assertIn("max-width: var(--shell);", body)
        self.assertNotIn("shell-breakout", body)
        self.assertNotIn("stats-scroll", body)
        self.assertNotIn("stats-table", body)
