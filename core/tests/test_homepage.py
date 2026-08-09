import re

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import resolve, reverse

from core import views as core_views
from courses.models.course import Course
from courses.views import course_list


class MainHomepageRoutingTests(TestCase):
    def test_root_uses_the_shared_course_platform_shell(self) -> None:
        self.assertEqual(reverse("home"), "/")
        self.assertIs(resolve("/").func, core_views.home)

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")
        self.assertContains(response, "Welcome to DataTalks.Club")
        self.assertContains(response, "The place to talk about data")
        self.assertContains(response, "Courses and cohorts")
        self.assertContains(response, '<link rel="canonical" href="https://datatalks.club/">')
        self.assertContains(response, "/static/courses.css")
        self.assertContains(response, "/static/core/site_shell.css")
        self.assertNotContains(response, "/static/core/site.css")

    def test_homepage_navigation_is_local_and_complete(self) -> None:
        response = self.client.get(reverse("home"))

        self.assertContains(response, 'aria-label="Primary navigation"')
        for route_name in (
            "events",
            "course_list",
            "articles",
            "podcast",
            "podwiki-home",
            "books",
            "docs-home",
            "faq-home",
            "slack",
        ):
            with self.subTest(route_name=route_name):
                self.assertContains(response, f'href="{reverse(route_name)}"')

        anchor_destinations = re.findall(
            r'<a\s[^>]*href="([^"]+)"',
            response.content.decode(),
        )
        for destination in anchor_destinations:
            self.assertFalse(destination.startswith("https://datatalks.club/"))
            self.assertFalse(destination.startswith("https://courses.datatalks.club/"))

    def test_cmp_account_is_the_only_shared_shell_login(self) -> None:
        anonymous_response = self.client.get(reverse("home"))
        self.assertContains(anonymous_response, f'href="{reverse("login")}"')
        self.assertContains(anonymous_response, "Login")

        user_model = get_user_model()
        user = user_model.objects.create(
            username="reviewer",
            email="reviewer@example.invalid",
        )
        self.client.force_login(user)
        authenticated_response = self.client.get(reverse("articles"))

        self.assertContains(authenticated_response, "reviewer@example.invalid")
        self.assertContains(
            authenticated_response,
            f'href="{reverse("account_settings")}"',
        )
        self.assertContains(
            authenticated_response,
            f'href="{reverse("account_logout")}"',
        )
        self.assertNotContains(authenticated_response, 'title="Login"')

    def test_staff_account_menu_links_studio_and_preserves_course_admin(self) -> None:
        user = get_user_model().objects.create(
            username="operator",
            email="operator@example.invalid",
            is_staff=True,
        )
        self.client.force_login(user)

        response = self.client.get(reverse("home"))

        self.assertContains(response, f'href="{reverse("studio:home")}"')
        self.assertContains(response, f'href="{reverse("cadmin_course_list")}"')

    def test_course_discovery_delegates_to_cmp_context(self) -> None:
        self.assertEqual(reverse("course_list"), "/courses/")
        self.assertIs(resolve("/courses/").func, course_list.course_list)

        response = self.client.get(reverse("course_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<title>Courses — DataTalks.Club</title>", html=True)
        self.assertContains(response, "Learn data skills. For free. Together.")
        self.assertContains(response, "AI Dev Tools Zoomcamp")
        self.assertContains(
            response,
            '<link rel="canonical" href="https://datatalks.club/courses/">',
        )

    @override_settings(ROOT_URLCONF="course_management.urls")
    def test_course_discovery_template_remains_compatible_with_copied_urlconf(self) -> None:
        response = self.client.get(reverse("course_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Learn data skills. For free. Together.")
        self.assertNotContains(response, "AI Dev Tools Zoomcamp")

    def test_database_backed_cmp_course_path_remains_intact(self) -> None:
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
