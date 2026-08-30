from pathlib import Path

from django.urls import reverse

from courses.tests.dashboard_view_base import DashboardViewTestBase


class DashboardViewTestCase(DashboardViewTestBase):
    def dashboard_url(self):
        return reverse(
            "dashboard",
            kwargs={
                "course_slug": self.course.course.slug,
                "cohort_year": self.course.year,
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

    def test_dense_tables_use_bounded_breakout_and_conditional_overflow_cues(self):
        body = (Path(__file__).resolve().parents[1] / "templates/courses/dashboard.html").read_text(
            encoding="utf-8"
        )

        self.assertIn('class="stats-scroll stats-scroll-wide shell-breakout"', body)
        self.assertEqual(body.count('class="stats-scroll stats-scroll-wide shell-breakout"'), 2)
        self.assertIn('class="stats-table stats-table-wide"', body)
        self.assertIn('id="homework-statistics-overflow" class="stats-overflow-cue" hidden', body)
        self.assertIn("frame.scrollWidth > frame.clientWidth + 1", body)
        self.assertIn('frame.setAttribute("tabindex", "0")', body)
        self.assertIn('frame.removeAttribute("tabindex")', body)
