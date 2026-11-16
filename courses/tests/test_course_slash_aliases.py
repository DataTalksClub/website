"""Trailing-slash spellings of course pages redirect once to their canonical.

Links carrying a CDN-added trailing slash exist in the wild.  The canonical course pages are
slashless (``/courses/<family>/<cohort-or-module>`` and
``/courses/<family>/<module>/<lesson>``); the slashed spelling is a one-hop permanent redirect
that keeps the raw query, and only for a page the canonical actually serves.
"""

from __future__ import annotations

from django.urls import resolve

from courses.models import Course
from courses.tests.test_shared_course_routes import SharedWorldTestCase


class CoursePageSlashAliasTests(SharedWorldTestCase):
    def assert_one_hop(self, source: str, target: str) -> None:
        for method in (self.client.get, self.client.head):
            for query in ("", "?utm_source=newsletter&cohort=2026"):
                with self.subTest(method=method.__name__, source=source, query=query):
                    response = method(f"{source}{query}", follow=False)
                    self.assertEqual(response.status_code, 301)
                    self.assertEqual(response.headers["Location"], f"{target}{query}")
                    self.assertEqual(response.headers["Cache-Control"], "public, max-age=300")
        terminal = self.client.get(target, follow=False)
        self.assertEqual(terminal.status_code, 200)

    def test_cohort_module_and_lesson_slash_spellings_redirect_once(self) -> None:
        cohort = f"/courses/{self.course.slug}/{self.cohort_2026.identifier}"
        self.assert_one_hop(f"{cohort}/", cohort)
        self.assert_one_hop(f"{self.module_url()}/", self.module_url())
        self.assert_one_hop(f"{self.lesson_url()}/", self.lesson_url())

    def test_canonical_course_urls_are_unchanged(self) -> None:
        self.assertEqual(self.module_url(), f"/courses/{self.course.slug}/{self.module.slug}")
        self.assertFalse(self.lesson_url().endswith("/"))
        self.assertEqual(self.client.get(self.lesson_url()).status_code, 200)

    def test_unknown_or_unpublished_targets_are_real_404s_without_a_redirect(self) -> None:
        self.lesson.published = False
        self.lesson.save(update_fields=["published"])
        hidden_family = Course.objects.create(slug="hidden-family", title="Hidden", visible=False)
        for path in (
            f"/courses/{self.course.slug}/no-such-cohort/",
            f"/courses/no-such-family/{self.cohort_2026.identifier}/",
            f"{self.lesson_url()}/",
            f"{self.module_url()}/no-such-lesson/",
            f"/courses/{hidden_family.slug}/{self.module.slug}/",
            f"/courses/{self.course.slug}/{self.cohort_2026.identifier}//",
        ):
            with self.subTest(path=path):
                response = self.client.get(path, follow=False)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn("Location", response.headers)

    def test_unsafe_methods_and_credential_queries_are_refused(self) -> None:
        source = f"/courses/{self.course.slug}/{self.cohort_2026.identifier}/"
        self.assertEqual(self.client.post(source).status_code, 405)
        refused = self.client.get(f"{source}?token=secret", follow=False)
        self.assertEqual(refused.status_code, 400)
        self.assertNotIn("Location", refused.headers)
        self.assertIn("no-store", refused.headers["Cache-Control"])

    def test_literal_slashed_course_routes_still_win(self) -> None:
        # The fallback is declared after every course route, so a literal slashed route such as
        # the registration campaign never reaches it.
        self.assertEqual(
            resolve("/courses/register/some-campaign/").url_name, "registration_campaign"
        )
        self.assertEqual(resolve("/courses/wrapped/2025/").url_name, "wrapped")
        self.assertEqual(
            resolve(f"/courses/{self.course.slug}/2026/").url_name, "course-page-slash-redirect"
        )
