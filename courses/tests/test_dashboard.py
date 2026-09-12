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

    def test_page_uses_the_shared_narrow_shell_like_its_sibling_task_surfaces(self):
        # A follow-up review (project-owner feedback, "too wide ... it's not
        # a landing page") replaced the page's own `.content-shell { max-width:
        # var(--shell); }` override -- the site's sole use of the wide 76rem
        # shell outside the homepage -- with the ordinary default every other
        # task surface actually renders at, homework statistics and the
        # leaderboard family included: neither overrides `.content-shell`,
        # so both already sit at the narrow, 56rem `--content-width`. The
        # per-section `.shell-breakout` escapes and the horizontal-scroll
        # wide table both stay gone -- reshaping into a row-list, not
        # scrolling sideways, is still how this page handles data too wide
        # for one line.
        body = (Path(__file__).resolve().parents[1] / "templates/courses/dashboard.html").read_text(
            encoding="utf-8"
        )

        self.assertNotIn(".content-shell {", body)
        self.assertNotIn("max-width: var(--shell);", body)
        self.assertNotIn("shell-breakout", body)
        self.assertNotIn("stats-scroll", body)
        self.assertNotIn("stats-table", body)
