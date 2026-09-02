"""Trailing-slash behaviour of the public course URL surface.

``/courses/<family>/`` used to 404 with "No Cohort matches the given query"
because the one-segment legacy alias ``<slug:course_slug>/`` matched the
family slug and looked it up as a cohort slug.  These tests pin the
slashless-canonical convention (a 301 from the slashed form) at the family
level, and pin the legacy edition-slug redirect that shares the same route so
the fix cannot silently swallow it.
"""

from django.test import TestCase
from django.urls import resolve

from courses.models import Cohort, Course
from courses.views.course import course_family_view, course_view
from courses.views.course_aliases import legacy_course_redirect


class CourseTrailingSlashTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        # ``Cohort.save`` derives the family slug by stripping the year, so this
        # gives a family slug ("slash-camp") that is not also a cohort slug.
        cls.cohort = Cohort.objects.create(
            title="Slash Camp 2026",
            slug="slash-camp-2026",
            description="Trailing-slash routing fixture",
            visible=True,
        )
        cls.family = cls.cohort.course

    def test_family_slug_fixture_is_distinct_from_the_cohort_slug(self) -> None:
        self.assertEqual(self.family.slug, "slash-camp")
        self.assertEqual(self.cohort.slug, "slash-camp-2026")
        self.assertEqual(self.cohort.identifier, "2026")

    def test_family_page_with_a_trailing_slash_redirects_to_the_slashless_page(self) -> None:
        response = self.client.get("/courses/slash-camp/")

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.headers["Location"], "/courses/slash-camp")

    def test_family_slash_redirect_preserves_the_query_string(self) -> None:
        response = self.client.get("/courses/slash-camp/?utm_source=x&x=%2F")

        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response.headers["Location"],
            "/courses/slash-camp?utm_source=x&x=%2F",
        )

    def test_the_family_redirect_target_is_the_family_page_and_does_not_redirect_again(
        self,
    ) -> None:
        response = self.client.get("/courses/slash-camp/", follow=True)

        self.assertEqual(response.redirect_chain, [("/courses/slash-camp", 301)])
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "courses/course_family.html")

    def test_legacy_edition_slug_with_a_trailing_slash_still_redirects_to_the_cohort(
        self,
    ) -> None:
        for alias in ("/courses/slash-camp-2026/", "/slash-camp-2026/"):
            with self.subTest(alias=alias):
                response = self.client.get(alias)

                self.assertEqual(response.status_code, 301)
                self.assertEqual(response.headers["Location"], "/courses/slash-camp/2026")

    def test_an_unknown_slug_with_a_trailing_slash_still_404s(self) -> None:
        for alias in ("/courses/no-such-course/", "/no-such-course/"):
            with self.subTest(alias=alias):
                response = self.client.get(alias, follow=True)

                self.assertEqual(response.redirect_chain, [])
                self.assertEqual(response.status_code, 404)

    def test_an_invisible_family_with_a_trailing_slash_still_404s(self) -> None:
        Course.objects.create(slug="hidden-camp", title="Hidden Camp", visible=False)

        self.assertEqual(self.client.get("/courses/hidden-camp").status_code, 404)
        self.assertEqual(self.client.get("/courses/hidden-camp/").status_code, 404)

    def test_the_slashless_canonical_routes_still_resolve_to_their_own_views(self) -> None:
        self.assertIs(resolve("/courses/slash-camp").func, course_family_view)
        self.assertIs(resolve("/courses/slash-camp/2026").func, course_view)
        self.assertIs(resolve("/courses/slash-camp/").func, legacy_course_redirect)

        family = self.client.get("/courses/slash-camp")
        self.assertEqual(family.status_code, 200)
        self.assertTemplateUsed(family, "courses/course_family.html")

        cohort = self.client.get("/courses/slash-camp/2026")
        self.assertEqual(cohort.status_code, 200)
        self.assertTemplateUsed(cohort, "courses/course.html")

    def test_the_alias_rejects_unsafe_methods_rather_than_redirecting_them(self) -> None:
        response = self.client.post("/courses/slash-camp/")

        self.assertEqual(response.status_code, 405)
