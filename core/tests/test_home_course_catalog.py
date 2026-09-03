"""The public course surfaces read the database, not a checked projection.

Issue #307: ``core.home_content.course_catalog`` and the featured panel used to read
``content/public_projection/courses.json``, so the homepage kept advertising the pinned
2025 cohorts while the database held live 2026 ones.  These tests build cohorts directly
rather than through ``courses.services.local_course_seed``, whose rows come from the same
pinned upstream revision that built the artefact and would therefore hide the defect.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from django.db import OperationalError
from django.test import TestCase
from django.urls import reverse

from content.public_data import public_projection
from core.home_content import FEATURED_FAMILY, course_catalog
from courses.models.cohort import Cohort, Course
from test_support.course_catalog import (
    build_reviewed_catalog,
    drop_cohort,
    make_cohort,
    make_family,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROJECTION_COURSES = REPOSITORY_ROOT / "content/public_projection/courses.json"


class CourseCatalogSourceTests(TestCase):
    def test_the_checked_course_projection_has_no_runtime_reader(self) -> None:
        """The artefact stays in the tree, byte-identical, with nothing reading it."""

        self.assertTrue(PROJECTION_COURSES.exists())
        for module in (
            REPOSITORY_ROOT / "core/home_content.py",
            REPOSITORY_ROOT / "core/views.py",
            REPOSITORY_ROOT / "content/review_views.py",
        ):
            with self.subTest(module=module.name):
                source = module.read_text(encoding="utf-8")
                self.assertNotIn('public_projection()["courses"]', source)
                self.assertNotIn('review_projection()["course"]', source)

    def test_an_empty_database_renders_an_empty_catalogue_rather_than_raising(self) -> None:
        self.assertEqual(Cohort.objects.count(), 0)

        self.assertEqual(course_catalog(), ())

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "data-featured-course")
        self.assertNotContains(response, 'class="card course-card')
        self.assertContains(response, "No active courses right now.")

    def test_an_unreachable_database_still_answers_the_homepage(self) -> None:
        from unittest import mock

        with mock.patch(
            "courses.services.public_course_catalog.visible_course_list_queryset",
            side_effect=OperationalError("unable to open database file"),
        ):
            self.assertEqual(course_catalog(), ())


class CourseCatalogSelectionTests(TestCase):
    def test_each_family_shows_its_newest_visible_cohort(self) -> None:
        build_reviewed_catalog()

        by_family = {entry.family: entry for entry in course_catalog()}

        self.assertEqual(by_family["ml-zoomcamp"].public_path, "/courses/ml-zoomcamp/2026")
        self.assertEqual(by_family["ml-zoomcamp"].cohort_label, "2026 cohort")
        self.assertEqual(by_family["llm-zoomcamp"].public_path, "/courses/llm-zoomcamp/2026")
        self.assertEqual(by_family["de-zoomcamp"].public_path, "/courses/de-zoomcamp/2026")
        self.assertEqual(by_family["mlops-zoomcamp"].public_path, "/courses/mlops-zoomcamp/2025")
        self.assertEqual(by_family["sma-zoomcamp"].public_path, "/courses/sma-zoomcamp/2025")

    def test_the_split_ai_dev_tools_family_collapses_to_one_2026_card(self) -> None:
        build_reviewed_catalog()

        catalog = course_catalog()
        ai_dev_tools = [entry for entry in catalog if entry.family == FEATURED_FAMILY]

        self.assertEqual(len(ai_dev_tools), 1)
        self.assertEqual(ai_dev_tools[0].slug, "ai-dev-tools-zoomcamp-2026")
        self.assertEqual(ai_dev_tools[0].public_path, "/courses/ai-dev-tools-zoomcamp/2026")
        self.assertEqual(len(catalog), 6)
        self.assertEqual([entry.title for entry in catalog].count("AI Dev Tools Zoomcamp"), 1)

    def test_a_hidden_cohort_falls_back_to_the_previous_edition(self) -> None:
        family = make_family("solo-zoomcamp", "Solo Zoomcamp")
        make_cohort(family, 2025, start_date=date(2025, 3, 1))
        make_cohort(family, 2026, start_date=date(2026, 3, 1), visible=False)

        by_family = {entry.family: entry for entry in course_catalog()}

        self.assertEqual(by_family["solo-zoomcamp"].public_path, "/courses/solo-zoomcamp/2025")

    def test_a_hidden_course_leaves_the_catalogue_entirely(self) -> None:
        family = make_family("hidden-zoomcamp", "Hidden Zoomcamp", visible=False)
        make_cohort(family, 2026)

        self.assertEqual(course_catalog(), ())

    def test_a_family_absent_from_the_presentation_table_still_renders(self) -> None:
        family = make_family("newcomer-zoomcamp", "Newcomer Zoomcamp")
        make_cohort(family, 2026, start_date=date(2026, 4, 2), homework_count=2)

        catalog = course_catalog()

        self.assertEqual(len(catalog), 1)
        self.assertEqual(catalog[0].title, "Newcomer Zoomcamp")
        self.assertEqual(catalog[0].public_path, "/courses/newcomer-zoomcamp/2026")

    def test_two_cohorts_in_one_year_resolve_deterministically(self) -> None:
        family = make_family("twin-zoomcamp", "Twin Zoomcamp")
        make_cohort(family, 2025, start_date=date(2025, 1, 1))
        # Same family, same year is barred by a unique constraint, so the tie is between
        # the newest year and an older one that started later in the calendar.
        later = make_cohort(family, 2026, slug="twin-zoomcamp-b", start_date=date(2026, 1, 1))

        catalog = course_catalog()

        self.assertEqual(catalog[0].slug, later.slug)

    def test_only_visible_cohorts_of_visible_courses_are_counted(self) -> None:
        build_reviewed_catalog()

        catalog = course_catalog()

        visible = set(
            Cohort.objects.filter(visible=True, course__visible=True).values_list("slug", flat=True)
        )
        for entry in catalog:
            with self.subTest(entry=entry.slug):
                self.assertIn(entry.slug, visible)


class HomepageCourseRenderingTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        build_reviewed_catalog()

    def test_the_homepage_links_every_family_to_its_newest_cohort(self) -> None:
        response = self.client.get(reverse("home"))
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        for path in (
            "/courses/ml-zoomcamp/2026",
            "/courses/llm-zoomcamp/2026",
            "/courses/de-zoomcamp/2026",
            "/courses/mlops-zoomcamp/2025",
            "/courses/sma-zoomcamp/2025",
        ):
            with self.subTest(path=path):
                self.assertIn(f'href="{path}"', body)
        for superseded in ("/courses/ml-zoomcamp/2025", "/courses/llm-zoomcamp/2025"):
            with self.subTest(superseded=superseded):
                self.assertNotIn(f'href="{superseded}"', body)

    def test_every_course_link_on_the_homepage_resolves(self) -> None:
        body = self.client.get(reverse("home")).content.decode()

        for entry in course_catalog():
            with self.subTest(path=entry.public_path):
                # Every family's resolved cohort page answers 200 and is linked from the
                # homepage: the catalogue cards link to it directly, and the featured
                # family's hero call to action goes to the same page.
                self.assertEqual(self.client.get(entry.public_path).status_code, 200)
                self.assertIn(f'href="{entry.public_path}"', body)

    def test_the_featured_panel_omits_a_zero_project_count(self) -> None:
        response = self.client.get(reverse("home"))
        body = response.content.decode()

        featured = body[body.index("data-featured-course") :]
        featured = featured[: featured.index("catalog-scroller")]

        self.assertIn("4 homework assignments", " ".join(featured.split()))
        self.assertNotIn("0 project", featured)
        self.assertIn("certificate of completion", featured)
        self.assertEqual(body.count("data-featured-course"), 1)

    def test_the_featured_panel_counts_modules_from_the_database(self) -> None:
        """The advertised module count follows the cohort's imported curriculum.

        The panel's editorial summary once carried a hand-written "Six modules", taken
        from the previous edition's docs page, while the database held the 2026 cohort's
        four.  The count is a database fact now, so the two cannot disagree.
        """

        body = self.client.get(reverse("home")).content.decode()
        featured = body[body.index("data-featured-course") :]
        featured = " ".join(featured[: featured.index("catalog-scroller")].split())

        self.assertIn("4 modules ·", featured)
        self.assertNotIn("Six modules", featured)

    def test_the_featured_panel_omits_a_module_count_the_database_lacks(self) -> None:
        """A cohort whose curriculum is not imported yet claims no modules at all."""

        drop_cohort("ai-dev-tools-zoomcamp-2026")
        family = Course.objects.get(slug="ai-dev-tools-zoomcamp")
        make_cohort(family, 2026, start_date=date(2026, 8, 31), homework_count=4)

        body = self.client.get(reverse("home")).content.decode()
        featured = body[body.index("data-featured-course") :]
        featured = " ".join(featured[: featured.index("catalog-scroller")].split())

        self.assertNotIn("0 module", featured)
        self.assertNotIn("module", featured)
        self.assertIn("4 homework assignments", featured)

    def test_the_featured_panel_makes_a_single_count_singular(self) -> None:
        drop_cohort("ai-dev-tools-zoomcamp-2026")
        family = Course.objects.get(slug="ai-dev-tools-zoomcamp")
        make_cohort(family, 2026, start_date=date(2026, 8, 31), homework_count=1, project_count=1)

        body = self.client.get(reverse("home")).content.decode()
        featured = body[body.index("data-featured-course") :]
        featured = " ".join(featured[: featured.index("catalog-scroller")].split())

        self.assertIn("1 homework assignment ·", featured)
        self.assertIn("1 project ·", featured)

    def test_no_course_or_cohort_description_markup_reaches_the_page(self) -> None:
        family = Course.objects.get(slug="ai-dev-tools-zoomcamp")
        family.description = (
            '<img src="https://example.invalid/banner.png"> '
            "See https://courses.datatalks.club/ai-dev-tools-zoomcamp/"
        )
        family.save(update_fields=["description"])
        cohort = Cohort.objects.get(slug="ai-dev-tools-zoomcamp-2026")
        cohort.description = "The 2026 live delivery of AI Dev Tools Zoomcamp."
        cohort.save(update_fields=["description"])

        body = self.client.get(reverse("home")).content.decode()

        self.assertNotIn("example.invalid", body)
        self.assertNotIn("courses.datatalks.club", body)
        self.assertNotIn("live delivery of AI Dev Tools Zoomcamp", body)

    def test_the_closing_line_counts_the_cards_it_stands_under(self) -> None:
        body = self.client.get(reverse("home")).content.decode()

        rendered_cards = body.count('class="card course-card')
        featured = 1 if "data-featured-course" in body else 0
        self.assertEqual(rendered_cards + featured, 6)
        self.assertIn("One free account. Six courses.", " ".join(body.split()))


class CourseSitemapSourceTests(TestCase):
    """The courses sitemap has always been database-backed; keep it that way."""

    def test_no_course_sitemap_entry_comes_from_the_checked_projection(self) -> None:
        build_reviewed_catalog()
        projection_paths = {str(record["public_path"]) for record in public_projection()["courses"]}
        self.assertTrue(projection_paths)

        response = self.client.get("/sitemaps/courses.xml")
        self.assertEqual(response.status_code, 200)
        locations = set(
            re.findall(r"<loc>https://datatalks\.club([^<]*)</loc>", response.content.decode())
        )

        self.assertTrue(locations)
        self.assertEqual(locations & projection_paths, set())
        # Every course entry is a family path the database supplied; none is a
        # `<family>/<year>` path taken from the artefact, and none is a hardcoded
        # per-cohort route.
        family_slugs = set(
            Cohort.objects.filter(visible=True, course__visible=True).values_list(
                "course__slug", flat=True
            )
        )
        self.assertEqual(
            locations,
            {"/courses"} | {f"/courses/{slug}" for slug in family_slugs},
        )
