import re
from datetime import timedelta
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import resolve, reverse
from django.utils import timezone

from content import public_views, review_views
from core import views as core_views
from courses.models.course import Course

REPO_ROOT = Path(__file__).resolve().parents[2]
ADOPTED_COURSE_LIST_TEMPLATE = (REPO_ROOT / "courses/templates/courses/course_list.html").resolve()


class MainHomepageRoutingTests(TestCase):
    def test_root_uses_the_shared_course_platform_shell(self) -> None:
        self.assertEqual(reverse("home"), "/")
        self.assertIs(resolve("/").func, core_views.home)

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")
        self.assertContains(response, "Welcome to DataTalks.Club")
        self.assertContains(response, "The place to talk about data")
        self.assertContains(response, "Courses")
        self.assertContains(response, "AI Dev Tools Zoomcamp")
        self.assertContains(response, "2026 cohort")
        self.assertContains(response, "Starts August 31, 2026")
        self.assertContains(
            response,
            f'href="{reverse("course-cohort-ai-dev-tools-2026")}"',
        )
        self.assertContains(response, "View cohort →")
        self.assertContains(response, "Browse all courses →")
        self.assertNotContains(response, "Data Engineering Zoomcamp 2026")
        self.assertEqual(
            len(re.findall(r"\sdata-featured-course(?=[\s>])", response.content.decode())),
            1,
        )
        self.assertContains(response, '<link rel="canonical" href="https://datatalks.club/">')
        self.assertRegex(response.content.decode(), r"/static/courses(?:\.[0-9a-f]+)?\.css")
        self.assertRegex(response.content.decode(), r"/static/core/site_shell(?:\.[0-9a-f]+)?\.css")
        self.assertNotContains(response, "/static/core/site.css")

    def test_homepage_navigation_is_local_and_complete(self) -> None:
        response = self.client.get(reverse("home"))

        self.assertContains(response, 'aria-label="Primary navigation"')
        for route_name in (
            "events",
            "course_list",
            "articles",
            "podcast",
            "wiki-home",
            "books",
            "docs-home",
            "faq-home",
            "slack",
        ):
            with self.subTest(route_name=route_name):
                self.assertContains(response, f'href="{reverse(route_name)}"')

        self.assertNotContains(response, 'href="/people"')

        anchor_destinations = re.findall(
            r'<a\s[^>]*href="([^"]+)"',
            response.content.decode(),
        )
        for destination in anchor_destinations:
            self.assertFalse(destination.startswith("https://datatalks.club/"))
            self.assertFalse(destination.startswith("https://courses.datatalks.club/"))

    def test_cmp_account_is_the_only_shared_shell_login(self) -> None:
        anonymous_response = self.client.get(reverse("home"))
        self.assertContains(
            anonymous_response,
            f'href="{reverse("login")}?next=%2F"',
        )
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

    def test_course_operator_menu_uses_studio_as_the_management_entrypoint(self) -> None:
        from accounts.studio_roles import synchronize_studio_roles

        user = get_user_model().objects.create(
            username="operator",
            email="operator@example.invalid",
            is_staff=True,
        )
        groups = {group.name: group for group in synchronize_studio_roles()}
        user.groups.add(groups["course_operator"])
        self.client.force_login(user)

        response = self.client.get(reverse("home"))

        self.assertContains(response, f'href="{reverse("studio:home")}"')
        self.assertNotContains(
            response,
            f'href="{reverse("studio_courses_course_list")}"',
        )
        self.assertNotContains(response, "Course admin")

    def test_course_discovery_without_database_courses_uses_the_public_catalog(self) -> None:
        self.assertEqual(reverse("course_list"), "/courses")
        self.assertIs(resolve("/courses").func, public_views.course_hub)
        self.assertFalse(Course.objects.exists())

        response = self.client.get(reverse("course_list"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "public/course_hub.html")
        self.assertNotIn(
            ADOPTED_COURSE_LIST_TEMPLATE,
            {
                Path(template.origin.name).resolve()
                for template in response.templates
                if template.origin is not None
            },
        )
        self.assertContains(response, "<title>Courses — DataTalks.Club</title>", html=True)
        self.assertContains(response, "Learn data skills. For free. Together.")
        self.assertContains(response, "Data Engineering Zoomcamp 2026")
        self.assertEqual(response.content.decode().count("data-course-row"), 12)
        self.assertNotContains(response, "md:grid-cols-2")
        self.assertContains(
            response,
            '<link rel="canonical" href="https://datatalks.club/courses">',
        )

    def test_course_discovery_with_database_courses_uses_copied_cmp_composition(self) -> None:
        today = timezone.localdate()
        active = Course.objects.create(
            title="Synthetic active course",
            slug="synthetic-active-course",
            description="A deterministic active course.",
            start_date=today - timedelta(days=7),
            end_date=today + timedelta(days=28),
            visible=True,
        )
        registration = Course.objects.create(
            title="Synthetic registration course",
            slug="synthetic-registration-course",
            description="A deterministic registration course.",
            start_date=today + timedelta(days=14),
            end_date=today + timedelta(days=70),
            registration_url="https://example.invalid/register",
            visible=True,
        )
        archived = Course.objects.create(
            title="Synthetic archived course 2024",
            slug="synthetic-archived-course-2024",
            description="A deterministic archived course.",
            finished=True,
            visible=True,
        )

        response = self.client.get(reverse("course_list"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "courses/course_list.html")
        self.assertIn(
            ADOPTED_COURSE_LIST_TEMPLATE,
            {
                Path(template.origin.name).resolve()
                for template in response.templates
                if template.origin is not None
            },
        )
        content = response.content.decode()
        self.assertLess(content.index("Active courses"), content.index(active.title))
        self.assertLess(content.index(active.title), content.index("Open registration"))
        self.assertLess(content.index("Open registration"), content.index(registration.title))
        self.assertLess(content.index(registration.title), content.index("Course archive"))
        self.assertLess(content.index("Course archive"), content.index(archived.title))
        self.assertContains(response, "Start now")
        self.assertContains(response, "Registration open")
        self.assertNotContains(response, 'id="course-families-heading"')
        self.assertNotContains(response, "No active cohort coursework right now.")
        self.assertContains(
            response,
            '<link rel="canonical" href="https://datatalks.club/courses">',
        )

    def test_database_backed_empty_visible_catalog_uses_cmp_empty_state(self) -> None:
        Course.objects.create(
            title="Synthetic hidden course",
            slug="synthetic-hidden-course",
            description="A deterministic hidden course.",
            visible=False,
        )

        response = self.client.get(reverse("course_list"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "courses/course_list.html")
        self.assertContains(response, "No active courses right now.")
        self.assertNotContains(response, "No active cohort coursework right now.")
        self.assertNotContains(response, "Synthetic hidden course")

    def test_ai_dev_tools_course_family_uses_the_same_cohort_row_hierarchy(self) -> None:
        path = reverse("course-family-ai-dev-tools")
        self.assertEqual(path, "/courses/ai-dev-tools-zoomcamp")
        self.assertIs(resolve(path).func, review_views.course_family)

        response = self.client.get(path)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "AI Dev Tools Zoomcamp")
        self.assertContains(response, "2026 cohort")
        self.assertContains(response, "Starts August 31, 2026")
        self.assertContains(response, "View cohort →")
        self.assertEqual(
            len(re.findall(r"\sdata-featured-course(?=[\s>])", response.content.decode())),
            1,
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
