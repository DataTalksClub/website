"""Catalogue facet rules and their import script (site feedback 3.1)."""

import json
import tempfile
from pathlib import Path

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase

from courses import catalog_facets
from courses.models import Course
from scripts.prod import import_course_catalog_facets


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
