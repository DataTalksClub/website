"""The family landing's pure composers: syllabus rows, stories, project cards.

These compose the page's facts from model-shaped rows without touching the
database, so the tests do either: every input here is a stub that carries the
attributes the composer reads, nothing more.
"""

from types import SimpleNamespace

from django.test import SimpleTestCase

from courses.course_page_content import (
    family_capstone_project,
    family_project_cards,
    family_story_rows,
    family_syllabus_rows,
)


class FamilySyllabusRowsTests(SimpleTestCase):
    def test_numbers_units_and_strips_the_spoken_module_prefix(self):
        units = [
            SimpleNamespace(title="Module 1: Agentic RAG", summary=""),
            SimpleNamespace(title="Module 2: Vector Search", summary="Embeddings."),
        ]

        rows = family_syllabus_rows(units)

        self.assertEqual([row.index for row in rows], ["01", "02"])
        self.assertEqual([row.title for row in rows], ["Agentic RAG", "Vector Search"])
        self.assertEqual(rows[1].summary, "Embeddings.")

    def test_strips_homework_prefixes_too(self):
        rows = family_syllabus_rows(
            [SimpleNamespace(title="Homework 3: Orchestration", summary="")]
        )

        self.assertEqual(rows[0].title, "Orchestration")

    def test_a_case_variant_is_still_a_prefix(self):
        rows = family_syllabus_rows([SimpleNamespace(title="module 10: Capstone", summary="")])

        self.assertEqual(rows[0].title, "Capstone")

    def test_an_unprefixed_title_passes_through(self):
        rows = family_syllabus_rows([SimpleNamespace(title="Capstone project", summary="")])

        self.assertEqual(rows[0].title, "Capstone project")


class FamilyStoryRowsTests(SimpleTestCase):
    def _testimonial(self, **overrides):
        row = SimpleNamespace(
            name="Alex",
            quote="Built a thing.",
            attribution="LLM Zoomcamp graduate",
            source_url="",
            portrait_url="",
        )
        for name, value in overrides.items():
            setattr(row, name, value)
        return row

    def test_flattens_the_fields_the_band_renders(self):
        story = family_story_rows([self._testimonial()])[0]

        self.assertEqual(story.name, "Alex")
        self.assertEqual(story.quote, "Built a thing.")
        self.assertEqual(story.attribution, "LLM Zoomcamp graduate")

    def test_reads_the_portrait_through_the_model_not_the_key(self):
        story = family_story_rows([self._testimonial(portrait_url="/static/core/x.jpg")])[0]

        self.assertEqual(story.portrait_url, "/static/core/x.jpg")


class FamilyProjectCardsTests(SimpleTestCase):
    def test_takes_the_newest_edition_that_holds_projects(self):
        newer_empty = SimpleNamespace(cohort=SimpleNamespace(identifier="2026"), projects=[])
        older_full = SimpleNamespace(
            cohort=SimpleNamespace(identifier="2025"),
            projects=[SimpleNamespace(slug="capstone-1"), SimpleNamespace(slug="capstone-2")],
        )

        cards = family_project_cards([newer_empty, older_full])

        self.assertEqual(
            [(card.cohort.identifier, card.project.slug) for card in cards],
            [("2025", "capstone-1"), ("2025", "capstone-2")],
        )

    def test_no_edition_holds_projects(self):
        empty = SimpleNamespace(cohort=SimpleNamespace(identifier="2026"), projects=[])

        self.assertEqual(family_project_cards([empty]), ())


class FamilyCapstoneProjectTests(SimpleTestCase):
    def test_the_last_project_is_the_capstone(self):
        projects = [SimpleNamespace(slug="midterm"), SimpleNamespace(slug="capstone")]

        self.assertEqual(family_capstone_project(projects).slug, "capstone")

    def test_no_projects_no_capstone(self):
        self.assertIsNone(family_capstone_project([]))
