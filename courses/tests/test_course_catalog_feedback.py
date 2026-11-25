"""The /courses catalogue after site feedback 3.1 (_docs/audits/2026-11-19-site-feedback.md)."""

import json
import re
import tempfile
from datetime import timedelta
from pathlib import Path

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from courses import catalog_facets
from courses.models import Cohort, Course, Testimonial, TestimonialPlacement
from scripts.prod import import_course_catalog_facets


def _empty_state_tag(html: str) -> str:
    """The opening tag of the catalogue's no-match hint."""
    match = re.search(r"<div[^>]*data-catalog-empty[^>]*>", html)
    assert match is not None
    return match.group(0)


def _card(html: str, title: str) -> str:
    title_at = html.index(f">{title}</a>")
    start = html.rindex("<article", 0, title_at)
    return html[start : html.index("</article>", title_at)]


class CatalogFacetRulesTests(SimpleTestCase):
    def test_parse_filters_reads_known_keys_and_dedupes(self):
        selection = catalog_facets.parse_filters(
            {"topic": "mlops, LLM-AI,mlops", "level": "", "bogus": "x"}
        )
        self.assertEqual(selection, {"topic": ("mlops", "llm-ai")})

    def test_or_within_a_filter_and_across_filters(self):
        values = {"topic": ("mlops",), "level": ("experienced",), "tool": ("docker",)}
        self.assertTrue(catalog_facets.card_matches(values, {"topic": ("mlops", "llm-ai")}))
        self.assertTrue(
            catalog_facets.card_matches(values, {"topic": ("mlops",), "tool": ("docker",)})
        )
        self.assertFalse(
            catalog_facets.card_matches(values, {"topic": ("mlops",), "level": ("beginner",)})
        )

    def test_duration_label_and_bucket(self):
        self.assertEqual(catalog_facets.duration_label(4), "4 weeks")
        self.assertEqual(catalog_facets.duration_label(19), "About 4 months")
        self.assertEqual(catalog_facets.length_bucket(4), "short")
        self.assertEqual(catalog_facets.length_bucket(10), "medium")
        self.assertEqual(catalog_facets.length_bucket(19), "long")
        self.assertEqual(catalog_facets.length_bucket(None), "")

    def test_validators_refuse_unknown_fits_and_duplicate_tools(self):
        with self.assertRaises(ValidationError):
            catalog_facets.validate_catalog_fits(["newbie"])
        with self.assertRaises(ValidationError):
            catalog_facets.validate_catalog_tools(["Docker", "docker"])
        catalog_facets.validate_catalog_tools(["Docker", "LLM APIs"])
        self.assertEqual(catalog_facets.tool_key("LLM APIs"), "llm-apis")


class CourseCatalogPageTests(TestCase):
    def setUp(self):
        today = timezone.localdate()
        self.de = Course.objects.create(
            slug="de-zoomcamp",
            title="Data Engineering Zoomcamp",
            catalog_topic="data-engineering",
            catalog_level="beginner",
            catalog_duration_weeks=10,
            catalog_tools=["Docker", "dbt"],
            catalog_project="An end-to-end data pipeline",
            catalog_fits=["new-to-data"],
        )
        self.mlops = Course.objects.create(
            slug="mlops-zoomcamp",
            title="MLOps Zoomcamp",
            catalog_topic="mlops",
            catalog_level="experienced",
            catalog_duration_weeks=4,
            catalog_hours_per_week="8–10",
            catalog_tools=["MLflow", "Docker"],
            catalog_fits=["models"],
        )
        Cohort.objects.create(
            course=self.de,
            slug="de-zoomcamp-2027",
            identifier="2027",
            title="Data Engineering Zoomcamp 2027",
            start_date=today + timedelta(days=40),
            end_date=today + timedelta(days=110),
            registration_url="https://example.invalid/register",
        )
        Cohort.objects.create(
            course=self.mlops,
            slug="mlops-zoomcamp-2026",
            identifier="2026",
            title="MLOps Zoomcamp 2026",
            start_date=today - timedelta(days=7),
            end_date=today + timedelta(days=21),
        )

    def html(self, **query):
        response = self.client.get(reverse("course_list"), query)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_sections_follow_the_feedback_order(self):
        html = self.html()
        body = html[html.index("<body") :]
        order = [
            'id="courses-hero-heading"',
            'id="benefits-heading"',
            'id="how-it-works-heading"',
            'id="course-fit-heading"',
            'id="catalog-heading"',
            'id="courses-faq-heading"',
            'id="courses-closing-heading"',
        ]
        positions = [body.index(marker) for marker in order]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("How our zoomcamps work", body)

    def test_every_card_has_the_same_fields_in_order(self):
        html = self.html()
        card = _card(html, "Data Engineering Zoomcamp")
        fields = [
            "Registration open",
            "Starts ",
            "Data Engineering Zoomcamp",
            "Data Engineering · Beginner-friendly",
            "10 weeks",
            ">Docker<",
            "You will build",
            "View course →",
        ]
        positions = [card.index(field) for field in fields]
        self.assertEqual(positions, sorted(positions))
        running = _card(html, "MLOps Zoomcamp")
        self.assertIn("Running now", running)
        self.assertIn("4 weeks · 8–10 hours a week", running)

    def test_registration_opens_first_then_running(self):
        html = self.html()
        grid = html[html.index('class="card-grid card-grid-3 courses-catalog-list"') :]
        self.assertLess(grid.index("Data Engineering Zoomcamp"), grid.index("MLOps Zoomcamp"))

    def test_filters_offer_only_values_some_course_has(self):
        html = self.html()
        self.assertIn('data-filter-value="data-engineering"', html)
        self.assertIn('data-filter-value="mlops"', html)
        self.assertNotIn('data-filter-value="finance"', html)
        self.assertNotIn('data-filter-value="machine-learning"', html)
        self.assertIn('data-filter-value="mlflow"', html)

    def test_filters_persist_in_the_address_and_render_server_side(self):
        html = self.html(topic="mlops", tool="docker")
        de_card = _card(html, "Data Engineering Zoomcamp")
        mlops_card = _card(html, "MLOps Zoomcamp")
        self.assertRegex(de_card, r"data-filter-tool=\"docker dbt\"\s+hidden")
        self.assertNotIn("hidden", mlops_card.split(">", 1)[0])
        self.assertIn("1 of 2 courses", html)
        self.assertIn('class="active-chip"', html)
        # Each chip's link drops only itself; "Clear all" drops everything.
        self.assertIn('href="/courses?tool=docker#courses"', html)
        self.assertIn('href="/courses?topic=mlops#courses"', html)
        self.assertRegex(html, r'href="/courses#courses" rel="nofollow" data-filter-clear\s*>')

    def test_no_match_shows_a_hint_instead_of_an_empty_grid(self):
        html = self.html(topic="mlops", level="beginner")
        self.assertIn("0 of 2 courses", html)
        self.assertNotIn("hidden", _empty_state_tag(html))
        self.assertIn("No course matches all of these filters.", html)

    def test_the_no_match_hint_stays_hidden_while_courses_are_listed(self):
        html = self.html()
        self.assertIn("2 courses", html)
        self.assertRegex(_empty_state_tag(html), r"\shidden\b")

    def test_unknown_filter_values_are_dropped(self):
        html = self.html(topic="does-not-exist")
        self.assertNotIn('class="active-chip"', html)
        self.assertNotRegex(html, r"data-catalog-card[^>]*\shidden")

    def test_course_fit_helper_filters_the_catalogue(self):
        html = self.html()
        self.assertIn("new to data", html)
        self.assertIn('href="/courses?for=new-to-data#courses"', html)
        filtered = self.html(**{"for": "models"})
        self.assertRegex(_card(filtered, "Data Engineering Zoomcamp"), r"\shidden\s")
        self.assertIn("is-active", filtered[filtered.index("I already work with models") - 400 :])

    def test_faq_is_one_column_of_folds(self):
        html = self.html()
        faq = html[html.index('id="courses-faq-heading"') :]
        self.assertGreaterEqual(faq.count('<details class="faq-fold"'), 5)
        self.assertNotIn("faq-grid", html)
        self.assertIn("a free account unlocks homework, projects and certificates", faq)

    def test_reviews_band_shows_course_and_homepage_testimonials_once(self):
        Testimonial.objects.all().delete()
        Testimonial.objects.create(
            placement=TestimonialPlacement.HOMEPAGE,
            name="Home Learner",
            quote="It helped me land my first data job.",
            published=True,
            position=1,
        )
        Testimonial.objects.create(
            placement=TestimonialPlacement.COURSE,
            course=self.de,
            name="Course Learner",
            quote="It helped me land my first data job.",
            published=True,
            position=1,
        )
        Testimonial.objects.create(
            placement=TestimonialPlacement.COURSE,
            course=self.mlops,
            name="Ops Learner",
            quote="Deploying models finally clicked.",
            published=True,
            position=2,
        )
        html = self.html()
        band = html[html.index('id="courses-stories-heading"') :]
        self.assertEqual(band.count('class="story-quote"'), 2)
        self.assertIn("Home Learner", band)
        self.assertIn("Ops Learner", band)
        self.assertIn('class="story-context story-course">MLOps Zoomcamp<', band)

    def test_no_reviews_band_without_testimonials(self):
        Testimonial.objects.all().delete()
        self.assertNotIn('id="courses-stories-heading"', self.html())


class ImportCourseCatalogFacetsTests(TestCase):
    def write(self, payload) -> Path:
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(payload, handle)
        handle.close()
        self.addCleanup(Path(handle.name).unlink)
        return Path(handle.name)

    def test_applies_validated_facts_and_replays_cleanly(self):
        Course.objects.create(slug="de-zoomcamp", title="DE")
        path = self.write(
            {
                "de-zoomcamp": {
                    "topic": "data-engineering",
                    "level": "beginner",
                    "duration_weeks": 10,
                    "tools": ["Docker", "dbt"],
                    "project": "A pipeline",
                    "fits": ["new-to-data"],
                },
                "missing-family": {"topic": "mlops"},
            }
        )
        dry = import_course_catalog_facets.run(path=path, apply=False)
        self.assertEqual(dry["would_update"], 1)
        self.assertEqual(Course.objects.get(slug="de-zoomcamp").catalog_topic, "")

        report = import_course_catalog_facets.run(path=path)
        self.assertEqual(report["updated"], 1)
        self.assertEqual(report["missing_slugs"], ["missing-family"])
        family = Course.objects.get(slug="de-zoomcamp")
        self.assertEqual(family.catalog_tools, ["Docker", "dbt"])
        self.assertEqual(family.catalog_duration_weeks, 10)

        self.assertEqual(import_course_catalog_facets.run(path=path)["updated"], 0)

    def test_refuses_invalid_values_without_writing(self):
        Course.objects.create(slug="de-zoomcamp", title="DE")
        path = self.write({"de-zoomcamp": {"topic": "astrology", "fits": ["newbie"]}})
        with self.assertRaises(import_course_catalog_facets.CatalogFacetImportFailure):
            import_course_catalog_facets.run(path=path)
        self.assertEqual(Course.objects.get(slug="de-zoomcamp").catalog_topic, "")

    def test_refuses_unknown_keys(self):
        path = self.write({"de-zoomcamp": {"colour": "blue"}})
        with self.assertRaises(import_course_catalog_facets.CatalogFacetImportFailure):
            import_course_catalog_facets.run(path=path)
