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

from content.docs_projection import docs_projection
from core import views as core_views
from core.home_content import (
    COURSE_FAMILIES,
    FEATURED_BUILD_ITEMS,
    FEATURED_COHORT_SUMMARY,
    FEATURED_FAMILY,
    MEMBER_STORIES,
    course_catalog,
)
from courses.models.cohort import Cohort
from courses.views.course import course_view
from courses.views.course_aliases import legacy_course_redirect
from courses.views.course_list import course_list
from events.models import Event
from test_support.course_catalog import build_reviewed_catalog

REPO_ROOT = Path(__file__).resolve().parents[2]
ADOPTED_COURSE_LIST_TEMPLATE = (REPO_ROOT / "courses/templates/courses/course_list.html").resolve()
ADOPTED_COURSE_DETAIL_TEMPLATE = (REPO_ROOT / "courses/templates/courses/course.html").resolve()


class FeaturedBuildPanelTests(TestCase):
    """The mint "What you'll build" panel may only claim what the course teaches.

    The panel once advertised a multi-agent/RAG curriculum and "small groups of 6-8
    people"; none of that describes the AI Dev Tools Zoomcamp.  Every bullet is now
    anchored to a phrase in the course's own curriculum page, so a future edit that
    drifts back into invented marketing copy fails here instead of shipping.
    """

    CURRICULUM_PATH = "/docs/courses/ai-dev-tools-zoomcamp/curriculum/"

    # Each build item, and a phrase its module's curriculum entry actually contains.
    BUILD_ITEM_SOURCE_ANCHORS = (
        (
            "a full app with a frontend, backend, and database, deployed with CI/CD",
            "Build a full app (frontend, backend, database) with a coding assistant",
        ),
        (
            "your own coding agent that scaffolds and extends a Django project",
            "Build your own coding agent that scaffolds and extends a Django project",
        ),
        (
            "task automations with n8n, such as creating LinkedIn posts",
            "Automate tasks with n8n",
        ),
        (
            "a complete application of your own, end to end, as the final project",
            "Build a complete application of your own using AI tools, end to end",
        ),
    )

    def setUp(self) -> None:
        # The panel now renders from the database, so the cohort it describes must exist.
        super().setUp()
        build_reviewed_catalog()

    def _curriculum_body(self) -> str:
        for page in docs_projection()["pages"]:
            if page["public_path"] == self.CURRICULUM_PATH:
                return str(page["body"])
        self.fail(f"The docs projection has no {self.CURRICULUM_PATH} page.")

    def test_the_featured_summary_is_grounded_in_the_course_curriculum_page(self) -> None:
        """The panel's own sentence is held to the same standard as its bullets.

        It previously claimed the course runs "over four modules" and produces "a
        specification and a groomed backlog", which the curriculum page states nowhere.
        """

        body = self._curriculum_body()
        self.assertIn("six modules plus a final project", body)
        self.assertIn("Six modules and a final project", FEATURED_COHORT_SUMMARY)
        for invented in ("four modules", "groomed backlog", "specification"):
            with self.subTest(invented=invented):
                self.assertNotIn(invented, FEATURED_COHORT_SUMMARY)

        response = self.client.get(reverse("home"))

        self.assertContains(response, escape(FEATURED_COHORT_SUMMARY))
        self.assertNotContains(response, "Over four modules")

    def test_every_build_item_is_grounded_in_the_course_curriculum_page(self) -> None:
        body = self._curriculum_body()
        self.assertEqual(
            FEATURED_BUILD_ITEMS,
            tuple(item for item, _anchor in self.BUILD_ITEM_SOURCE_ANCHORS),
        )
        for item, anchor in self.BUILD_ITEM_SOURCE_ANCHORS:
            with self.subTest(item=item):
                self.assertIn(anchor, body)

    def test_the_panel_renders_the_build_items_and_no_group_size_claim(self) -> None:
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        for item in FEATURED_BUILD_ITEMS:
            with self.subTest(item=item):
                self.assertContains(response, escape(item[0].upper() + item[1:]))

        body = response.content.decode()
        self.assertNotIn("build-note", body)
        for retired in ("6–8", "6-8", "small groups", "RAG evaluation", "multi-agent"):
            with self.subTest(retired=retired):
                self.assertNotIn(retired, body)


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
        self.assertEqual(
            [(story.name, story.context) for story in MEMBER_STORIES],
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
        for name, context in [(story.name, story.context) for story in MEMBER_STORIES]:
            with self.subTest(name=name):
                self.assertContains(response, name)
                self.assertContains(response, escape(context))

    def test_root_uses_the_shared_course_platform_shell(self) -> None:
        # The catalogue and featured panel read ``courses.Course`` / ``courses.Cohort``,
        # so the families this asserts are built here rather than taken from
        # ``courses.services.local_course_seed``, whose rows are pinned to one revision.
        build_reviewed_catalog()
        self.assertEqual(reverse("home"), "/")
        self.assertIs(resolve("/").func, core_views.home)

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
        featured = next(
            entry for entry in course_catalog() if entry.family == FEATURED_FAMILY
        )
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
        ):
            response = self.client.get(reverse("home"))
            unified = self.client.get("/unified/")

        for rendered in (response, unified):
            self.assertEqual(rendered.status_code, 200)
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
        from core.home_content import MEMBER_STORIES

        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        expected_order = tuple(story.name for story in MEMBER_STORIES)
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
        from core.home_content import MemberStory

        synthetic_stories = (
            MemberStory(
                quote="Real quote from a member with a photo.",
                name="Has Photo",
                context="Role · City",
                photo_static_path="core/testimonials/tim-claytor.jpg",
            ),
            MemberStory(
                quote="Real quote from a member without a photo yet.",
                name="No Photo Yet",
                context="Role · City",
            ),
        )

        with mock.patch("core.views.MEMBER_STORIES", synthetic_stories):
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
