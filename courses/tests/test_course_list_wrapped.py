"""The courses index is the site's only entry point into Wrapped.

Nothing else in the site links to `wrapped`, so a rebuild of this page that drops
the block silently makes the whole feature unreachable.  These tests hold the
entry point in place: it appears under SHOW_WRAPPED, it points at the published
year, and it survives every catalogue filter.
"""

from django.test import TestCase, override_settings
from django.urls import reverse

from courses.models.wrapped import WrappedStatistics
from courses.views.course_list import WRAPPED_ENTRY_FALLBACK_YEAR


class CourseListWrappedEntryPointTests(TestCase):
    def course_list_response(self, **params):
        return self.client.get(reverse("course_list"), params)

    @override_settings(SHOW_WRAPPED=True)
    def test_courses_index_links_to_wrapped_when_the_flag_is_set(self):
        response = self.course_list_response()

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'href="{reverse("wrapped", args=[WRAPPED_ENTRY_FALLBACK_YEAR])}"',
        )
        self.assertContains(
            response,
            f"DataTalks.Club Wrapped {WRAPPED_ENTRY_FALLBACK_YEAR}",
        )
        self.assertContains(
            response,
            "Check out your year of learning in review",
        )
        self.assertContains(
            response,
            f"View your {WRAPPED_ENTRY_FALLBACK_YEAR} wrapped",
        )

    @override_settings(SHOW_WRAPPED=False)
    def test_courses_index_hides_wrapped_while_the_flag_is_off(self):
        response = self.course_list_response()

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["wrapped_year"])
        self.assertNotContains(response, "DataTalks.Club Wrapped")
        self.assertNotContains(response, reverse("wrapped", args=[2025]))

    @override_settings(SHOW_WRAPPED=True)
    def test_wrapped_entry_point_follows_the_newest_published_year(self):
        WrappedStatistics.objects.create(year=2026, is_visible=True)
        WrappedStatistics.objects.create(year=2027, is_visible=False)

        response = self.course_list_response()

        self.assertEqual(response.context["wrapped_year"], 2026)
        self.assertContains(response, f'href="{reverse("wrapped", args=[2026])}"')
        self.assertNotContains(response, reverse("wrapped", args=[2027]))

    @override_settings(SHOW_WRAPPED=True)
    def test_wrapped_entry_point_survives_every_catalogue_filter(self):
        wrapped_url = reverse("wrapped", args=[WRAPPED_ENTRY_FALLBACK_YEAR])
        for course_filter in ("all", "active", "open", "finished"):
            with self.subTest(filter=course_filter):
                response = self.course_list_response(filter=course_filter)

                self.assertContains(response, f'href="{wrapped_url}"')
