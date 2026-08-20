import re
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import OperationalError
from django.test import Client, TestCase, override_settings
from django.urls import resolve, reverse
from django.utils import timezone
from django.utils.html import escape

from core import views as core_views
from courses.models.cohort import Cohort
from courses.views.course import course_view
from courses.views.course_aliases import legacy_course_redirect
from courses.views.course_list import course_list
from events.models import Event

REPO_ROOT = Path(__file__).resolve().parents[2]
ADOPTED_COURSE_LIST_TEMPLATE = (REPO_ROOT / "courses/templates/courses/course_list.html").resolve()
ADOPTED_COURSE_DETAIL_TEMPLATE = (REPO_ROOT / "courses/templates/courses/course.html").resolve()


class MainHomepageRoutingTests(TestCase):
    def test_root_uses_the_shared_course_platform_shell(self) -> None:
        self.assertEqual(reverse("home"), "/")
        self.assertIs(resolve("/").func, core_views.home)

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")
        self.assertContains(response, "DataTalks.Club")
        self.assertContains(
            response,
            "Start with the foundations. Finish with a project you can present.",
        )
        self.assertContains(response, "Explore free courses")
        self.assertContains(response, "Join the community")
        self.assertContains(response, "From “What does that mean?” to “Let me show you.”")
        self.assertContains(response, "I don’t know where to start")
        self.assertContains(response, "I’m connecting the pieces")
        self.assertContains(response, "I can talk through my project")
        self.assertContains(response, "Courses")
        self.assertContains(response, "AI Dev Tools Zoomcamp")
        self.assertContains(response, "Starts August 31")
        self.assertContains(
            response,
            f'href="{reverse("course-cohort-ai-dev-tools-2026")}"',
        )
        self.assertContains(response, "View the syllabus")
        self.assertContains(response, "all courses →")
        self.assertEqual(
            len(re.findall(r"\sdata-featured-course(?=[\s>])", response.content.decode())),
            1,
        )
        self.assertContains(response, '<link rel="canonical" href="https://datatalks.club/">')
        self.assertNotContains(response, "/static/core/site.css")

    def test_homepage_carries_its_own_stylesheet_and_loads_no_legacy_css(self) -> None:
        """Design 5a (issue #179) replaced the adopted shell with one inline stylesheet."""

        body = self.client.get(reverse("home")).content.decode()

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

    def test_single_destination_cards_stretch_their_existing_semantic_links(self) -> None:
        body = self.client.get(reverse("home")).content.decode()

        self.assertIn('class="card course-card stretched-card-link"', body)
        self.assertIn('class="card latest-card stretched-card-link podcast-card"', body)
        self.assertIn(
            '<p class="mono-label mono-label-indigo podcast-meta">',
            body,
        )
        self.assertIn('class="card latest-card stretched-card-link">', body)
        self.assertIn(".stretched-card-link .latest-item h3 a::after", body)
        self.assertIn(".stretched-card-link .course-link::after", body)

    def test_homepage_leaks_no_unrendered_template_syntax(self) -> None:
        """A multi-line {# #} comment is not a comment, and reaches the reader as copy."""

        body = self.client.get(reverse("home")).content.decode()

        for leak in ("{#", "#}", "{%", "%}", "{{", "}}"):
            with self.subTest(token=leak):
                self.assertNotIn(leak, body)

    def test_homepage_renders_when_the_database_is_unavailable(self) -> None:
        """/unified/ is the container liveness gate, and that runtime has no database."""

        with mock.patch.object(
            Event.objects,
            "order_by",
            side_effect=OperationalError("unable to open database file"),
        ):
            response = self.client.get(reverse("home"))
            unified = self.client.get("/unified/")

        for rendered in (response, unified):
            self.assertEqual(rendered.status_code, 200)
            self.assertContains(
                rendered,
                "Start with the foundations. Finish with a project you can present.",
            )
            self.assertContains(rendered, "AI Dev Tools Zoomcamp")
            self.assertContains(rendered, "The wiki, as a graph")

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
        self.assertContains(anonymous_response, "Log in")

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

    def test_course_discovery_without_database_courses_uses_copied_cmp_empty_state(self) -> None:
        self.assertEqual(reverse("course_list"), "/courses")
        self.assertIs(resolve("/courses").func, course_list)
        self.assertFalse(Cohort.objects.exists())

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
        self.assertContains(response, "Learn data skills. For free. Together.")
        self.assertContains(response, "No active courses right now.")
        self.assertNotContains(response, "Data Engineering Zoomcamp 2026")
        self.assertContains(response, 'class="band band-cream courses-hero"')
        self.assertContains(response, 'id="courses"')
        self.assertContains(response, "Active now — you can still join")
        self.assertNotContains(response, "data-course-row")
        self.assertNotContains(response, "md:grid-cols-2")
        self.assertNotContains(response, 'id="course-families-heading"')
        self.assertNotContains(response, "No active cohort coursework right now.")
        self.assertContains(
            response,
            '<link rel="canonical" href="https://datatalks.club/courses">',
        )

    def test_course_index_carries_its_own_stylesheet_and_loads_no_legacy_css(self) -> None:
        """Design 5a (issue #179) rebuilt /courses onto one inline stylesheet."""

        body = self.client.get(reverse("course_list")).content.decode()

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
        for leak in ("{#", "#}", "{%", "%}", "{{", "}}"):
            with self.subTest(token=leak):
                self.assertNotIn(leak, body)

    def test_course_index_renders_all_sections_without_filters(self) -> None:
        """The course index shows the complete catalogue without filter controls."""

        today = timezone.localdate()
        Cohort.objects.create(
            title="Synthetic active course",
            slug="synthetic-active-course",
            description="A deterministic active course.",
            start_date=today - timedelta(days=7),
            end_date=today + timedelta(days=28),
            visible=True,
        )
        Cohort.objects.create(
            title="Synthetic registration course",
            slug="synthetic-registration-course",
            description="A deterministic registration course.",
            start_date=today + timedelta(days=14),
            end_date=today + timedelta(days=70),
            registration_url="https://example.invalid/register",
            visible=True,
        )

        everything = self.client.get(reverse("course_list"))
        self.assertNotContains(everything, "filter-pill")
        self.assertContains(everything, "Active now — you can still join")
        self.assertContains(everything, "Open registration")
        self.assertContains(everything, "Synthetic registration course")

        filtered_url = self.client.get(reverse("course_list"), {"filter": "active"})
        self.assertEqual(filtered_url.status_code, 200)
        self.assertContains(filtered_url, "Synthetic registration course")

    def test_course_discovery_with_database_courses_uses_copied_cmp_composition(self) -> None:
        today = timezone.localdate()
        active = Cohort.objects.create(
            title="Synthetic active course",
            slug="synthetic-active-course",
            description="A deterministic active course.",
            start_date=today - timedelta(days=7),
            end_date=today + timedelta(days=28),
            visible=True,
        )
        registration = Cohort.objects.create(
            title="Synthetic registration course",
            slug="synthetic-registration-course",
            description="A deterministic registration course.",
            start_date=today + timedelta(days=14),
            end_date=today + timedelta(days=70),
            registration_url="https://example.invalid/register",
            visible=True,
        )
        archived = Cohort.objects.create(
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
        catalog = content[content.index('<div id="courses">') :]
        active_heading = "Active now — you can still join"
        self.assertLess(catalog.index(active_heading), catalog.index(active.title))
        self.assertLess(catalog.index(active.title), catalog.index("Open registration"))
        self.assertLess(catalog.index("Open registration"), catalog.index(registration.title))
        self.assertLess(catalog.index(registration.title), catalog.index("Finished courses"))
        self.assertLess(catalog.index("Finished courses"), catalog.index(archived.title))
        self.assertContains(response, "registration open")
        self.assertNotContains(response, 'id="course-families-heading"')
        self.assertNotContains(response, "No active cohort coursework right now.")
        self.assertContains(
            response,
            '<link rel="canonical" href="https://datatalks.club/courses">',
        )

    def test_database_backed_empty_visible_catalog_uses_cmp_empty_state(self) -> None:
        Cohort.objects.create(
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

    @override_settings(ROOT_URLCONF="course_management.urls")
    def test_course_discovery_template_remains_compatible_with_copied_urlconf(self) -> None:
        response = self.client.get(reverse("course_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Learn data skills. For free. Together.")
        self.assertNotContains(response, "AI Dev Tools Zoomcamp")

    def test_database_backed_cmp_course_path_remains_intact(self) -> None:
        course = Cohort.objects.create(
            title="Compatibility course",
            slug="compatibility-course",
            description="Legacy inbound routing fixture",
            visible=True,
        )

        slash_alias = reverse("courses:course", kwargs={"course_slug": course.slug})
        canonical_path = reverse("course", kwargs={"course_slug": course.slug})
        legacy_path = reverse("legacy-course", kwargs={"course_slug": course.slug})

        self.assertEqual(slash_alias, "/courses/compatibility-course/")
        self.assertEqual(canonical_path, "/courses/compatibility-course")
        self.assertEqual(legacy_path, "/compatibility-course/")
        self.assertIs(resolve(canonical_path).func, course_view)
        self.assertIs(resolve(slash_alias).func, legacy_course_redirect)
        for alias in (slash_alias, legacy_path):
            response = self.client.get(f"{alias}?x=%2F&x=")
            self.assertEqual(response.status_code, 301)
            self.assertEqual(
                response.headers["Location"],
                f"{canonical_path}?x=%2F&x=",
            )
        canonical = self.client.get(canonical_path)
        self.assertEqual(canonical.status_code, 200)
        self.assertTemplateUsed(canonical, "courses/course.html")
        self.assertIn(
            ADOPTED_COURSE_DETAIL_TEMPLATE,
            {
                Path(template.origin.name).resolve()
                for template in canonical.templates
                if template.origin is not None
            },
        )
        self.assertContains(
            canonical,
            '<link rel="canonical" href="https://datatalks.club/courses/compatibility-course">',
            count=1,
        )
        csrf_client = Client(enforce_csrf_checks=True)
        self.assertEqual(csrf_client.post(slash_alias).status_code, 403)
        self.assertEqual(csrf_client.post(legacy_path).status_code, 403)

    def test_unknown_legacy_shaped_path_is_a_real_404(self) -> None:
        for path in ("/not-a-course/", "/courses/not-a-course"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")
                self.assertNotContains(response, "Traceback", status_code=404)
                self.assertNotContains(response, 'rel="canonical"', status_code=404)


class HomepageWikiGraphTests(TestCase):
    """The wiki-graph section draws real data as one scalable SVG per width."""

    def _rendered_layouts(self) -> dict[str, str]:
        body = self.client.get(reverse("home")).content.decode()
        chunks = {}
        for kind in ("wide", "narrow"):
            match = re.search(
                rf'<svg\s+class="graph-svg graph-svg-{kind}".*?</svg>',
                body,
                re.DOTALL,
            )
            if match is None:
                self.fail(f"homepage renders no {kind} wiki graph SVG")
            chunks[kind] = match.group(0)
        return chunks

    def test_every_node_is_a_link_to_its_real_wiki_page(self) -> None:
        from core.home_content import wiki_graph

        graph = wiki_graph()
        rendered = self._rendered_layouts()
        for layout in graph.layouts:
            svg = rendered[layout.kind]
            hub_href = re.escape(graph.hub.public_path)
            hub_anchor = re.search(
                rf'<a class="graph-svg-node graph-svg-hub" href="{hub_href}">.*?</a>',
                svg,
                re.DOTALL,
            )
            if hub_anchor is None:
                self.fail(f"{layout.kind}: hub is not a link")
            self.assertIn(escape(graph.hub.title), hub_anchor.group(0))
            for node in layout.nodes:
                with self.subTest(layout=layout.kind, topic=node.title):
                    # The href is the topic's own public path, and it routes.
                    self.assertEqual(resolve(node.url).url_name, "public-wiki")
                    href = re.escape(node.url)
                    anchor = re.search(
                        rf'<a class="graph-svg-node" href="{href}">.*?</a>',
                        svg,
                        re.DOTALL,
                    )
                    if anchor is None:
                        self.fail("node is not a link")
                    for line in node.lines:
                        self.assertIn(f">{escape(line.text)}</tspan>", anchor.group(0))

    def test_rendered_edge_count_matches_the_validated_relation_data(self) -> None:
        from core.home_content import wiki_graph

        graph = wiki_graph()
        rendered = self._rendered_layouts()
        for layout in graph.layouts:
            with self.subTest(layout=layout.kind):
                self.assertEqual(
                    len(re.findall(r'<line class="graph-svg-edge"', rendered[layout.kind])),
                    len(layout.edges),
                )
                self.assertEqual(len(layout.edges), len(graph.spokes))
        # The legend describes the drawing: the spokes drawn, out of the hub's
        # real relation count, never the other way around.
        body = self.client.get(reverse("home")).content.decode()
        self.assertIn(
            f"{len(graph.spokes)} of {graph.connections} "
            f"{escape(graph.hub.title)} connections drawn",
            body,
        )
