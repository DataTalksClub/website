import hashlib
import json
import re
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.storage import staticfiles_storage
from django.db import OperationalError, connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse
from django.utils import timezone
from django.utils.html import escape

from content.docs_projection import docs_projection
from core import views as core_views
from core.home_content import (
    COURSE_FAMILIES,
    FEATURED_BUILD_ITEMS,
    FEATURED_COHORT_SUMMARY,
    FEATURED_FAMILY,
    course_catalog,
)
from courses.models.cohort import Cohort, Course
from courses.models.testimonial import Testimonial, TestimonialPlacement
from courses.services.testimonials import homepage_testimonials
from courses.views.course import course_view
from courses.views.course_aliases import legacy_course_redirect
from courses.views.course_list import course_list
from events.models import Event
from test_support.course_catalog import build_reviewed_catalog

REPO_ROOT = Path(__file__).resolve().parents[2]
ADOPTED_COURSE_LIST_TEMPLATE = (REPO_ROOT / "courses/templates/courses/course_list.html").resolve()
ADOPTED_COURSE_DETAIL_TEMPLATE = (REPO_ROOT / "courses/templates/courses/course.html").resolve()

CURRICULUM_2026 = REPO_ROOT / "core/tests/data/ai_dev_tools_zoomcamp_2026"


class FeaturedBuildPanelTests(TestCase):
    """The mint "What you'll build" panel may only claim what the featured cohort teaches.

    Two generations of wrong copy have shipped here.  The first advertised a
    multi-agent/RAG curriculum and "small groups of 6-8 people", which describes no
    DataTalks.Club course.  The second described the 2025 edition -- "six modules", a
    coding agent you build, n8n automation -- because it was anchored to the course-wide
    docs page ``/docs/courses/ai-dev-tools-zoomcamp/curriculum/``, which still enumerates
    the 2025 modules.

    So the anchor is the featured cohort's own curriculum, not the course's: the four
    module lessons of ``cohorts/2026/`` copied verbatim into ``core/tests/data/`` with
    their revision and checksums.  Every clause the panel states is pinned to a phrase
    those lessons contain, so copy that drifts back into marketing -- or back into a
    previous edition -- fails here instead of shipping.
    """

    # The panel's sentence, and the phrases the 2026 lessons state it from.
    SUMMARY_SOURCE_ANCHORS = (
        ("01-ai-native-workflow", "AI-Native Development"),
        (
            "01-ai-native-workflow",
            "we take a vague product idea through specification and context",
        ),
        ("02-development", "you build a working end-to-end application with AI assistance"),
        ("03-deployment", "Test, Containerize, and Deploy an AI-Assisted App"),
        ("04-devops", "DevOps and Observability for AI-Built Apps"),
    )

    # Each build item, the 2026 module it comes from, and phrases that module's lesson
    # actually contains.  One item per module, in module order.
    BUILD_ITEM_SOURCE_ANCHORS = (
        (
            "a Django app built from a specification, with the AI tool of your choice",
            "01-ai-native-workflow",
            (
                "Build a Django app with the AI tool of your choice",
                "we take a vague product idea through specification and context",
            ),
        ),
        (
            "a full-stack app with a frontend, a backend, an OpenAPI contract, "
            "and data persisted in SQLite",
            "02-development",
            (
                "a frontend and a backend that talk to each other over a defined contract, "
                "with data persisted in SQLite",
                "Define an OpenAPI contract as the source of truth between frontend and backend",
            ),
        ),
        (
            "the same app containerized, integration-tested, and deployed at a public URL",
            "03-deployment",
            (
                "Containerize the app with a multi-stage Dockerfile and Docker Compose",
                "Write integration tests that hit a real database",
                "The app should be deployed at a public URL",
            ),
        ),
        (
            "an observability stack, an alert on real user impact, "
            "and an agent as first line of support",
            "04-devops",
            (
                "The concrete stack is OpenTelemetry into Prometheus, Loki, and Tempo, "
                "with Grafana on top",
                "Write one alert that represents real user impact",
                "put an agent inside that loop as the first line of support",
            ),
        ),
    )

    # Claims that belong to the 2025 edition (cohorts/2025) and to the invented copy before
    # it.  None of them may reappear in the panel.
    RETIRED_CLAIMS = (
        "6–8",
        "6-8",
        "small groups",
        "RAG evaluation",
        "multi-agent",
        "Six modules",
        "six modules",
        "n8n",
        "coding agent that scaffolds",
        "low-code",
    )

    def setUp(self) -> None:
        # The panel now renders from the database, so the cohort it describes must exist.
        super().setUp()
        build_reviewed_catalog()

    def _module_lessons(self) -> dict[str, str]:
        """Return each checked-in 2026 module lesson, whitespace-normalised for matching.

        The lessons are prose wrapped at the source's own column, so a sentence is only a
        contiguous string once its line breaks are collapsed.
        """

        manifest = json.loads((CURRICULUM_2026 / "SOURCE.json").read_text())
        lessons: dict[str, str] = {}
        for module in manifest["modules"]:
            raw = (CURRICULUM_2026 / str(module["file"])).read_bytes()
            self.assertEqual(
                hashlib.sha256(raw).hexdigest(),
                module["sha256"],
                f"{module['source_path']} no longer matches its recorded checksum.",
            )
            lessons[str(module["slug"])] = " ".join(raw.decode().split())
        return lessons

    def test_the_checked_curriculum_copy_is_the_2026_cohort(self) -> None:
        """The anchor source is the featured cohort's curriculum, pinned and identified."""

        manifest = json.loads((CURRICULUM_2026 / "SOURCE.json").read_text())

        self.assertEqual(
            manifest["source"]["repository"],
            "https://github.com/DataTalksClub/ai-dev-tools-zoomcamp",
        )
        self.assertEqual(manifest["source"]["cohort_path"], "cohorts/2026")
        self.assertRegex(manifest["source"]["revision"], r"^[0-9a-f]{40}$")
        self.assertEqual(
            tuple(str(module["slug"]) for module in manifest["modules"]),
            ("01-ai-native-workflow", "02-development", "03-deployment", "04-devops"),
        )
        lessons = self._module_lessons()
        for module in manifest["modules"]:
            with self.subTest(module=module["slug"]):
                self.assertTrue(str(module["source_path"]).startswith("cohorts/2026/"))
                self.assertIn(f"# {module['title']}", lessons[str(module["slug"])])

    def test_the_featured_summary_is_grounded_in_the_2026_module_lessons(self) -> None:
        """The panel's own sentence is held to the same standard as its bullets."""

        lessons = self._module_lessons()
        for module, anchor in self.SUMMARY_SOURCE_ANCHORS:
            with self.subTest(anchor=anchor):
                self.assertIn(anchor, lessons[module])
        for retired in self.RETIRED_CLAIMS:
            with self.subTest(retired=retired):
                self.assertNotIn(retired, FEATURED_COHORT_SUMMARY)
        # The module count is a database fact rendered beside the homework and project
        # counts; a sentence that states its own count is what shipped the 2025 curriculum.
        self.assertNotIn("module", FEATURED_COHORT_SUMMARY)

        response = self.client.get(reverse("home"))

        self.assertContains(response, escape(FEATURED_COHORT_SUMMARY))

    def test_every_build_item_is_grounded_in_its_2026_module_lesson(self) -> None:
        lessons = self._module_lessons()
        self.assertEqual(
            FEATURED_BUILD_ITEMS,
            tuple(item for item, _module, _anchors in self.BUILD_ITEM_SOURCE_ANCHORS),
        )
        for item, module, anchors in self.BUILD_ITEM_SOURCE_ANCHORS:
            for anchor in anchors:
                with self.subTest(item=item, anchor=anchor):
                    self.assertIn(anchor, lessons[module])

    def test_the_stale_course_docs_curriculum_is_not_the_panel_source(self) -> None:
        """The docs page this panel used to read still describes the 2025 edition.

        It is a course-wide page, not a cohort page, and DataTalksClub/docs has not
        refreshed it for 2026.  Pin that here so the drift is visible rather than
        rediscovered by copying from it again.
        """

        body = ""
        for page in docs_projection()["pages"]:
            if page["public_path"] == "/docs/courses/ai-dev-tools-zoomcamp/curriculum/":
                body = str(page["body"])
        self.assertIn("six modules plus a final project", body)
        self.assertIn("Automate tasks with n8n", body)
        for item in FEATURED_BUILD_ITEMS:
            with self.subTest(item=item):
                self.assertNotIn(item, body)

    def test_the_panel_renders_the_build_items_and_no_retired_claim(self) -> None:
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        for item in FEATURED_BUILD_ITEMS:
            with self.subTest(item=item):
                self.assertContains(response, escape(item[0].upper() + item[1:]))

        body = response.content.decode()
        self.assertNotIn("build-note", body)
        panel = body[body.index("data-featured-course") :]
        panel = panel[: panel.index("catalog-scroller")]
        for retired in self.RETIRED_CLAIMS:
            with self.subTest(retired=retired):
                self.assertNotIn(retired, panel)


class CatalogCardTests(TestCase):
    """The catalogue cards carry the course title, not an uppercase category pill."""

    def setUp(self) -> None:
        # The cards are database rows now, so the families have to exist to be shown.
        super().setUp()
        build_reviewed_catalog()

    def test_catalog_cards_drop_the_category_pill_and_keep_the_new_cohort_chip(self) -> None:
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        scroller = body[body.index('id="catalog-scroller"') :]
        scroller = scroller[: scroller.index("catalog-scroller-controls")]

        self.assertNotIn("chip", scroller)
        self.assertNotIn("featured-badges", body)
        for family, title in COURSE_FAMILIES:
            if family == FEATURED_FAMILY:
                continue
            with self.subTest(family=family):
                self.assertIn(escape(title), scroller)

        # The featured cohort keeps its own chip; only the category pills went.
        self.assertIn('<span class="chip chip-ink">New cohort</span>', body)


class MainHomepageRoutingTests(TestCase):
    def test_member_stories_keep_titles_country_only_and_requested_order(self) -> None:
        """The six seeded rows still read exactly as the retired Python tuple did."""

        stories = homepage_testimonials()
        self.assertEqual(
            [(story.name, story.attribution) for story in stories],
            [
                ("Nevenka Lukic", "Data Engineer · Spain"),
                ("Alexander Daniel Rios", "DS & ML Engineer · Argentina"),
                ("Jocelyn Dumlao", "Data Scientist · Philippines"),
                ("Zachary Keller", "Data & Analytics · United States"),
                ("Hanaa Hammad", "Senior Data Engineer · Egypt"),
                ("Tim Claytor", "Data Science · United States"),
            ],
        )

        response = self.client.get(reverse("home"))
        for name, attribution in [(story.name, story.attribution) for story in stories]:
            with self.subTest(name=name):
                self.assertContains(response, name)
                self.assertContains(response, escape(attribution))

    def test_root_uses_the_shared_course_platform_shell(self) -> None:
        # The catalogue and featured panel read ``courses.Course`` / ``courses.Cohort``,
        # so the families this asserts are built here rather than taken from
        # ``courses.services.local_course_seed``, whose rows are pinned to one revision.
        build_reviewed_catalog()
        self.assertEqual(reverse("home"), "/")
        self.assertIs(resolve("/").func, core_views.home)
        # /unified/ is the deployment's rendered-page probe and serves the same
        # view, so production robots keeps it out of the index (core.views).
        self.assertIs(resolve("/unified/").func, core_views.home)
        self.assertIn("Disallow: /unified/\n", core_views.PRODUCTION_ROBOTS_BODY)

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")
        self.assertContains(response, "DataTalks.Club")
        self.assertContains(
            response,
            "Learn the fundamentals. Build real projects. Share your work.",
        )
        self.assertContains(response, "Explore free courses")
        self.assertContains(response, "Join the community")
        self.assertContains(
            response,
            '<a class="cta cta-secondary interactive-lift" '
            f'href="{reverse("account_signup")}">Join the community</a>',
            html=True,
        )
        self.assertContains(response, "From “What does that mean?” to “Let me show you.”")
        self.assertContains(response, "I don’t know where to start")
        self.assertContains(response, "I’m connecting the pieces")
        self.assertContains(response, "I can talk through my project")
        self.assertContains(response, "Courses")
        self.assertContains(response, "AI Dev Tools Zoomcamp")
        self.assertContains(response, "Starts August 31")
        # The featured call to action goes to the cohort's own database-backed course
        # page, the same route the catalogue cards use.  The hardcoded per-cohort
        # landing route it used to point at has been removed.
        featured = next(entry for entry in course_catalog() if entry.family == FEATURED_FAMILY)
        self.assertContains(response, f'href="{featured.public_path}"')
        self.assertContains(response, "View the syllabus")
        self.assertContains(response, "all courses →")
        self.assertEqual(
            len(re.findall(r"\sdata-featured-course(?=[\s>])", response.content.decode())),
            1,
        )
        self.assertContains(response, '<link rel="canonical" href="https://datatalks.club/">')
        self.assertNotContains(response, "/static/core/site.css")

    def test_homepage_carries_its_own_stylesheet_and_loads_no_legacy_css(self) -> None:
        """Design system (issue #179) replaced the adopted shell with one inline stylesheet."""

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
        # The course card is a database row now, so the catalogue has to hold one.
        build_reviewed_catalog()
        body = self.client.get(reverse("home")).content.decode()

        self.assertIn(
            'class="card course-card stretched-card-link interactive-card interactive-lift"',
            body,
        )
        self.assertIn(
            "card latest-card stretched-card-link podcast-card interactive-card interactive-lift",
            body,
        )
        self.assertIn(
            '<p class="mono-label mono-label-indigo podcast-meta">',
            body,
        )
        self.assertIn(
            'class="card latest-card stretched-card-link interactive-card interactive-lift">',
            body,
        )
        self.assertIn(".stretched-card-link .latest-item h3 a::after", body)
        self.assertIn(".stretched-card-link .course-link::after", body)
        self.assertIn(">View course</a>", body)
        self.assertNotIn(">View course →</a>", body)

    def test_homepage_leaks_no_unrendered_template_syntax(self) -> None:
        """A multi-line {# #} comment is not a comment, and reaches the reader as copy."""

        body = self.client.get(reverse("home")).content.decode()

        for leak in ("{#", "#}", "{%", "%}", "{{", "}}"):
            with self.subTest(token=leak):
                self.assertNotIn(leak, body)

    def test_homepage_renders_when_the_database_is_unavailable(self) -> None:
        """/unified/ is the container liveness gate, and that runtime has no database.

        The course catalogue is a database read now, so an unreachable database costs the
        page its course cards and its featured panel.  It must still answer 200 with the
        designed empty state rather than propagate the error.
        """

        with (
            mock.patch.object(
                Event.objects,
                "order_by",
                side_effect=OperationalError("unable to open database file"),
            ),
            mock.patch(
                "courses.services.public_course_catalog.visible_course_list_queryset",
                side_effect=OperationalError("unable to open database file"),
            ),
            mock.patch.object(
                Testimonial.objects,
                "filter",
                side_effect=OperationalError("unable to open database file"),
            ),
        ):
            response = self.client.get(reverse("home"))
            unified = self.client.get("/unified/")

        for rendered in (response, unified):
            self.assertEqual(rendered.status_code, 200)
            # The testimonial band is a database read too, so it drops out whole
            # rather than leaving an empty band behind.
            self.assertNotContains(rendered, 'id="stories-heading"')
            self.assertContains(
                rendered,
                "Learn the fundamentals. Build real projects. Share your work.",
            )
            self.assertContains(rendered, "No active courses right now.")
            self.assertNotContains(rendered, "data-featured-course")
            self.assertContains(rendered, "Community knowledgebase")

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
        self.assertRegex(
            response.content.decode(),
            r'class="band band-cream content-page-header\s+courses-hero\s*"',
        )
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
        """Design system (issue #179) rebuilt /courses onto one inline stylesheet."""

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
        # The shared design-system stylesheet still defines the primitive for
        # other catalogues. Assert that the course index does not render any
        # filter control, rather than rejecting the shared CSS definition.
        self.assertNotContains(everything, 'class="filter-pill')
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
        # The catalogue now collapses editions into one family row.  The
        # finished card therefore uses the family title while the edition
        # identifier remains in the action link.
        self.assertLess(catalog.index("Finished courses"), catalog.index(archived.course.title))
        self.assertContains(response, "Latest edition · 2024")
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

    def test_database_backed_course_family_and_cohort_paths_remain_intact(self) -> None:
        course = Cohort.objects.create(
            title="Compatibility course",
            slug="compatibility-course",
            description="Legacy inbound routing fixture",
            visible=True,
        )

        slash_alias = reverse("courses:course", kwargs={"course_slug": course.slug})
        family_path = reverse("course_family", kwargs={"course_slug": course.course.slug})
        canonical_path = reverse(
            "course",
            kwargs={
                "course_slug": course.course.slug,
                "cohort_year": course.identifier,
            },
        )
        legacy_path = reverse("legacy-course", kwargs={"course_slug": course.slug})

        self.assertEqual(slash_alias, "/courses/compatibility-course/")
        self.assertEqual(family_path, "/courses/compatibility-course")
        self.assertEqual(canonical_path, "/courses/compatibility-course/2026")
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
            '<link rel="canonical" href="https://datatalks.club/courses/compatibility-course/2026">',
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


class MemberStoriesCarouselTests(TestCase):
    """The "people who were exactly where you are" band is a carousel of six.

    Six real, sourced testimonials alternate man/woman; the section reuses the
    catalogue's `.scroller-button`/`.scroller-controls` carousel shape instead of
    a second bespoke pattern (see `.stories-scroller` in `_design_system.html`).
    """

    def test_all_six_stories_render_in_the_alternating_order(self) -> None:
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        expected_order = tuple(story.name for story in homepage_testimonials())
        self.assertEqual(len(expected_order), 6)
        positions = [body.index(name) for name in expected_order]
        self.assertEqual(positions, sorted(positions))

        self.assertEqual(
            len(re.findall(r'<figure class="card">', body)),
            len(expected_order),
        )

    def test_stories_render_inside_the_carousel_container_not_a_static_grid(self) -> None:
        response = self.client.get(reverse("home"))
        body = response.content.decode()

        heading_index = body.index('id="stories-heading"')
        section = body[heading_index : body.index("</section>", heading_index)]

        self.assertIn('class="stories-scroller"', section)
        self.assertIn('id="stories-scroller"', section)
        self.assertIn('role="group"', section)
        self.assertIn('aria-label="Member stories"', section)
        self.assertIn('tabindex="0"', section)
        # The static three-up grid is gone from this section; only the climb
        # explainer above it still uses the plain card-grid.
        self.assertNotIn('class="card-grid card-grid-3"', section)

    def test_carousel_controls_target_the_stories_scroller_and_reuse_the_shared_button(
        self,
    ) -> None:
        # The catalogue scroller this reuses only renders when the database holds courses.
        build_reviewed_catalog()
        response = self.client.get(reverse("home"))
        body = response.content.decode()

        for label in ("Scroll member stories left", "Scroll member stories right"):
            with self.subTest(label=label):
                self.assertIn(f'aria-label="{label}"', body)

        self.assertIn('data-scroll-target="stories-scroller"', body)
        self.assertEqual(
            body.count('data-scroll-target="stories-scroller"'),
            2,
        )
        # The controls reuse the exact same button/arrow markup the catalogue
        # scroller already uses, rather than a second bespoke control.
        self.assertIn('data-scroll-target="catalog-scroller"', body)

    def test_a_story_without_a_photo_falls_back_to_the_decorative_avatar(self) -> None:
        Testimonial.objects.filter(placement=TestimonialPlacement.HOMEPAGE).delete()
        Testimonial.objects.create(
            placement=TestimonialPlacement.HOMEPAGE,
            quote="Real quote from a member with a photo.",
            name="Has Photo",
            attribution="Role · City",
            portrait_asset_key="testimonials/tim-claytor.jpg",
            position=0,
            published=True,
        )
        Testimonial.objects.create(
            placement=TestimonialPlacement.HOMEPAGE,
            quote="Real quote from a member without a photo yet.",
            name="No Photo Yet",
            attribution="Role · City",
            position=1,
            published=True,
        )

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()

        with_photo = body[body.index("Has Photo") - 600 : body.index("Has Photo")]
        without_photo = body[body.index("No Photo Yet") - 600 : body.index("No Photo Yet")]

        self.assertIn(
            '<img class="avatar" src="/static/core/testimonials/tim-claytor.jpg"',
            with_photo,
        )
        self.assertNotIn('<img class="avatar"', without_photo)
        self.assertIn('<span class="avatar" aria-hidden="true"></span>', without_photo)

    def test_one_unresolvable_portrait_costs_one_photo_not_the_whole_page(self) -> None:
        """A row must never be able to 500 the homepage.

        Manifest static storage raises on an unknown reference instead of
        serving a 404, and the portrait is read inside the story loop, so an
        unguarded lookup would abandon the render for every reader.  The bad row
        keeps its card and falls back to the avatar mark; the good row is
        untouched.
        """

        Testimonial.objects.filter(placement=TestimonialPlacement.HOMEPAGE).delete()
        Testimonial.objects.create(
            placement=TestimonialPlacement.HOMEPAGE,
            quote="A quote from the member whose portrait resolves.",
            name="Good Portrait",
            attribution="Role · City",
            portrait_asset_key="testimonials/tim-claytor.jpg",
            position=0,
            published=True,
        )
        stale = Testimonial(
            placement=TestimonialPlacement.HOMEPAGE,
            quote="A quote from the member whose portrait went missing.",
            name="Stale Portrait",
            attribution="Role · City",
            portrait_asset_key="testimonials/removed-after-the-manifest-was-built.jpg",
            position=1,
            published=True,
        )
        # The key was valid when it was stored; the asset is what went away, so
        # this bypasses validation the way a real stale row reaches production.
        stale.save()

        real_url = staticfiles_storage.url

        def manifest_url(name: str) -> str:
            if "removed-after-the-manifest-was-built" in name:
                raise ValueError(f"Missing staticfiles manifest entry for '{name}'")
            return real_url(name)

        with mock.patch.object(staticfiles_storage, "url", side_effect=manifest_url):
            response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()

        self.assertIn("Good Portrait", body)
        self.assertIn("Stale Portrait", body)
        self.assertIn('<img class="avatar" src="/static/core/testimonials/tim-claytor.jpg"', body)
        self.assertNotIn("removed-after-the-manifest-was-built", body)
        stale_card = body[body.index("Stale Portrait") - 600 : body.index("Stale Portrait")]
        self.assertNotIn('<img class="avatar"', stale_card)
        self.assertIn('<span class="avatar" aria-hidden="true"></span>', stale_card)

    def test_the_band_disappears_whole_when_no_testimonial_is_published(self) -> None:
        """No empty band, no orphan heading, no dangling scroller."""

        Testimonial.objects.filter(placement=TestimonialPlacement.HOMEPAGE).delete()

        body = self.client.get(reverse("home")).content.decode()

        for absent in (
            'id="stories-heading"',
            "What people say",
            'class="stories-scroller"',
            'id="stories-scroller"',
            'aria-label="Member stories"',
            'aria-labelledby="stories-heading"',
            'data-scroll-target="stories-scroller"',
            "/static/core/testimonials/",
        ):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, body)

    def test_unpublished_and_course_scoped_testimonials_stay_off_the_homepage(self) -> None:
        course = Course.objects.create(slug="unlisted-family", title="Unlisted Family")
        Testimonial.objects.create(
            placement=TestimonialPlacement.HOMEPAGE,
            name="Draft Only Person",
            attribution="Role · City",
            quote="A draft quote that is not live yet.",
            published=False,
        )
        Testimonial.objects.create(
            placement=TestimonialPlacement.COURSE,
            course=course,
            name="Course Only Person",
            attribution="Role · City",
            quote="A quote that belongs to one course page.",
            published=True,
        )

        body = self.client.get(reverse("home")).content.decode()

        self.assertNotIn("Draft Only Person", body)
        self.assertNotIn("Course Only Person", body)
        self.assertIn("What people say", body)

    def test_the_band_costs_the_anonymous_homepage_exactly_one_query(self) -> None:
        with CaptureQueriesContext(connection) as captured:
            self.client.get(reverse("home"))

        testimonial_queries = [
            query["sql"]
            for query in captured.captured_queries
            if "courses_testimonial" in query["sql"]
        ]
        self.assertEqual(len(testimonial_queries), 1)


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
        self.assertRegex(
            body,
            rf"{len(graph.spokes)}\s+of\s+{graph.connections}\s+"
            rf"{escape(graph.hub.title)}\s+connections drawn",
        )

    def test_graph_explorer_loads_the_public_graph_and_keeps_a_no_js_fallback(self) -> None:
        from core.home_content import wiki_graph

        graph = wiki_graph()
        body = self.client.get(reverse("home")).content.decode()
        self.assertIn("data-home-graph", body)
        self.assertIn(f'data-graph-url="{reverse("wiki-graph-json")}"', body)
        self.assertIn(f'data-start-id="wiki:{graph.hub.slug}"', body)
        self.assertIn('src="/static/core/home_graph.js"', body)
        self.assertIn("Click a neighbour to move there.", body)
        self.assertIn("data-home-graph-fallback", body)
        self.assertIn("data-home-graph-live hidden", body)
        self.assertIn("data-home-graph-random", body)
        self.assertIn(f'href="{graph.hub.public_path}"', body)
