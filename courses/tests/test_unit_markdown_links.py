import uuid
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from courses.models import Cohort, Course, CurriculumFormat, Homework, Module, Unit


class UnitMarkdownLinkTests(TestCase):
    def setUp(self):
        self.course = Course.objects.create(slug="llm-zoomcamp", title="LLM Zoomcamp")
        self.cohort = Cohort.objects.create(
            course=self.course,
            identifier="2026",
            slug="llm-zoomcamp-2026",
            year=2026,
            title="LLM Zoomcamp 2026",
            description="A module-format cohort.",
            curriculum_format=CurriculumFormat.MODULES,
        )
        self.module = self.make_module(
            slug="01-agentic-rag",
            title="Module 1: Agentic RAG",
            source_path="cohorts/2026/01-agentic-rag/module.yaml",
        )
        self.previous = self.make_unit(
            module=self.module,
            slug="04-dataset",
            title="The Course FAQ Dataset",
            source_path="cohorts/2026/01-agentic-rag/lessons/04-dataset.md",
        )
        self.current = self.make_unit(
            module=self.module,
            slug="05-search",
            title="Search",
            source_path="cohorts/2026/01-agentic-rag/lessons/05-search.md",
        )
        self.next = self.make_unit(
            module=self.module,
            slug="06-building-prompt",
            title="Building the Prompt",
            source_path="cohorts/2026/01-agentic-rag/lessons/06-building-prompt.md",
        )
        self.other_module = self.make_module(
            slug="02-vector-search",
            title="Module 2: Vector Search",
            source_path="cohorts/2026/02-vector-search/module.yaml",
        )
        self.other_unit = self.make_unit(
            module=self.other_module,
            slug="01-intro",
            title="What is Vector Search",
            source_path="cohorts/2026/02-vector-search/lessons/01-intro.md",
        )

    @staticmethod
    def provenance(source_path):
        return {
            "source_content_id": uuid.uuid4(),
            "source_path": source_path,
            "source_commit_sha": "a" * 40,
            "source_checksum": "b" * 64,
        }

    def make_module(self, *, slug, title, source_path):
        homework = Homework.objects.create(
            course=self.cohort,
            slug=f"{slug}-homework",
            title=f"{title} Homework",
            due_date=timezone.now() + timedelta(days=7),
        )
        return Module.objects.create(
            cohort=self.cohort,
            position=Module.objects.filter(cohort=self.cohort).count() + 1,
            slug=slug,
            title=title,
            terminal_homework=homework,
            **self.provenance(source_path),
        )

    def make_unit(self, *, module, slug, title, source_path):
        return Unit.objects.create(
            module=module,
            position=Unit.objects.filter(module=module).count() + 1,
            slug=slug,
            title=title,
            **self.provenance(source_path),
        )

    def unit_url(self, unit):
        return reverse(
            "unit",
            kwargs={
                "course_slug": self.course.slug,
                "cohort_identifier": self.cohort.identifier,
                "module_slug": unit.module.slug,
                "unit_slug": unit.slug,
            },
        )

    def test_repository_navigation_links_use_canonical_unit_urls(self):
        self.current.content_markdown = (
            "[← The Course FAQ Dataset](04-dataset.md#faq) | "
            "[Building the Prompt →](06-building-prompt.md)"
        )
        self.current.save(update_fields=["content_markdown"])

        response = self.client.get(self.unit_url(self.current))

        self.assertContains(
            response,
            f"{self.unit_url(self.previous)}#faq",
        )
        self.assertContains(response, self.unit_url(self.next))
        self.assertNotContains(response, "04-dataset.md#faq")
        self.assertNotContains(response, "06-building-prompt.md")

    def test_cross_module_readme_and_external_links_keep_their_intended_targets(self):
        self.current.content_markdown = (
            "[Vector Search](../../02-vector-search/lessons/01-intro.md)\n\n"
            "[Vector module](../../02-vector-search/README.md)\n\n"
            "[External](https://example.com/lesson.md)\n\n"
            "![diagram](04-dataset.md)\n\n"
            "[Missing](99-missing.md)"
        )
        self.current.save(update_fields=["content_markdown"])

        response = self.client.get(self.unit_url(self.current))

        self.assertContains(response, self.unit_url(self.other_unit))
        self.assertContains(
            response,
            reverse(
                "module",
                kwargs={
                    "course_slug": self.course.slug,
                    "cohort_identifier": self.cohort.identifier,
                    "module_slug": self.other_module.slug,
                },
            ),
        )
        self.assertContains(response, "https://example.com/lesson.md")
        self.assertContains(response, "99-missing.md")
        # An image reference is never turned into a unit route, and this course
        # family has no repository to resolve it against, so only its
        # description survives.
        self.assertNotContains(response, 'src="04-dataset.md"')
        self.assertContains(response, "diagram")

    def homework_url(self, homework):
        return reverse(
            "homework",
            kwargs={
                "course_slug": self.course.slug,
                "cohort_year": self.cohort.identifier,
                "homework_slug": homework.slug,
            },
        )

    def test_a_link_to_the_instructions_file_lands_on_the_homework_page(self):
        """The homework record states the Markdown it was imported from.

        Without that path nothing can connect a lesson's ``homework.md`` link
        to the page that publishes those instructions, so the link 404s.
        """

        homework = self.module.terminal_homework
        homework.instructions_source_path = "cohorts/2026/01-agentic-rag/homework.md"
        homework.save(update_fields=["instructions_source_path"])
        self.current.content_markdown = "[Homework](../homework.md#question-1)"
        self.current.save(update_fields=["content_markdown"])

        response = self.client.get(self.unit_url(self.current))

        self.assertContains(response, f"{self.homework_url(homework)}#question-1")
        self.assertNotContains(response, "../homework.md")

    def test_homework_beside_a_module_manifest_lands_on_that_modules_homework(self):
        """The ML curriculum keeps ``homework.md`` next to ``module.yaml``."""

        self.current.content_markdown = "[Homework](../../02-vector-search/homework.md)"
        self.current.save(update_fields=["content_markdown"])

        response = self.client.get(self.unit_url(self.current))

        self.assertContains(
            response,
            self.homework_url(self.other_module.terminal_homework),
        )

    def test_an_unrelated_markdown_file_is_not_mistaken_for_homework(self):
        self.current.content_markdown = "[Notes](../notes.md)"
        self.current.save(update_fields=["content_markdown"])

        response = self.client.get(self.unit_url(self.current))

        self.assertContains(response, "../notes.md")
