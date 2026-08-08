from django.test import TestCase
from django.urls import resolve, reverse

from core import views as core_views
from courses.models.course import Course
from courses.views import course_list


class MainHomepageRoutingTests(TestCase):
    def test_root_and_unified_alias_use_the_distinct_main_site_shell(self) -> None:
        self.assertEqual(reverse("home"), "/")
        self.assertEqual(reverse("unified-home"), "/unified/")
        self.assertIs(resolve("/").func, core_views.home)

        for path in (reverse("home"), reverse("unified-home")):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")
                self.assertContains(response, "<title>Welcome to DataTalks.Club</title>")
                self.assertContains(response, "The place to talk about data")
                self.assertContains(
                    response,
                    "Global online community of data science professionals, ML engineers, "
                    "and AI practitioners",
                )
                self.assertContains(
                    response,
                    '<link rel="canonical" href="https://datatalks.club/">',
                    count=1,
                )
                self.assertContains(response, "/static/core/site.css")
                self.assertNotContains(response, "Learn data skills. For free. Together.")
                self.assertNotContains(response, "cdn.tailwindcss.com")
                self.assertNotContains(response, "googletagmanager")
                self.assertNotContains(response, "google-analytics")

    def test_main_navigation_uses_working_transitional_destinations(self) -> None:
        response = self.client.get(reverse("home"))

        for destination in (
            "https://datatalks.club/articles.html",
            "https://datatalks.club/slack.html",
            "https://datatalks.club/events.html",
            "https://datatalks.club/podcast.html",
            "https://datatalks.club/podwiki/",
            "https://datatalks.club/books.html",
            reverse("course_list"),
        ):
            with self.subTest(destination=destination):
                self.assertContains(response, f'href="{destination}"')

    def test_course_discovery_moves_forward_under_courses_namespace(self) -> None:
        self.assertEqual(reverse("course_list"), "/courses/")
        self.assertIs(resolve("/courses/").func, course_list.course_list)

        response = self.client.get(reverse("course_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Learn data skills. For free. Together.")
        self.assertNotContains(response, "The place to talk about data")
        self.assertNotContains(response, 'rel="canonical"')

    def test_nonempty_legacy_course_path_remains_an_inbound_alias(self) -> None:
        course = Course.objects.create(
            title="Compatibility course",
            slug="compatibility-course",
            description="Legacy inbound routing fixture",
            visible=True,
        )
        forward_path = reverse("courses:course", kwargs={"course_slug": course.slug})
        legacy_path = reverse("course", kwargs={"course_slug": course.slug})

        self.assertEqual(forward_path, "/courses/compatibility-course/")
        self.assertEqual(legacy_path, "/compatibility-course/")
        self.assertEqual(self.client.get(forward_path).status_code, 200)
        self.assertEqual(self.client.get(legacy_path).status_code, 200)

    def test_unknown_legacy_shaped_path_is_a_real_404(self) -> None:
        response = self.client.get("/not-a-course/")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")
        self.assertNotContains(response, "Traceback", status_code=404)
        self.assertNotContains(response, 'rel="canonical"', status_code=404)
