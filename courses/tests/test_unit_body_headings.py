"""A unit body must not repeat the title the page already prints as its `h1`.

Course repositories open a lesson file with its own title, at whichever heading
level suited the file: the LLM lessons write `# Introduction`, the ML lessons
write `## 1.1 Introduction to Machine Learning`.  Only the first form was ever
removed, so 103 of the imported ML lessons rendered their title twice.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from courses.models import Cohort, Course, CurriculumFormat, Homework, Module, Unit


class UnitLeadingHeadingTests(TestCase):
    def setUp(self):
        self.course_family = Course.objects.create(slug="ml-zoomcamp", title="ML Zoomcamp")
        self.cohort = Cohort.objects.create(
            course=self.course_family,
            slug="ml-zoomcamp-2026",
            identifier="2026",
            year=2026,
            title="ML Zoomcamp 2026",
            description="A module-format cohort.",
            curriculum_format=CurriculumFormat.MODULES,
            github_repo_url="https://github.com/DataTalksClub/machine-learning-zoomcamp.git",
        )
        self.homework = Homework.objects.create(
            course=self.cohort,
            slug="intro-homework",
            title="Intro Homework",
            due_date=timezone.now() + timedelta(days=7),
        )
        self.module = Module.objects.create(
            cohort=self.cohort,
            position=10,
            slug="01-intro",
            title="Introduction to Machine Learning",
            terminal_homework=self.homework,
        )
        self.unit = Unit.objects.create(
            module=self.module,
            position=10,
            slug="01-what-is-ml",
            title="1.1 Introduction to Machine Learning",
            source_content_id=uuid.uuid4(),
            source_path="01-intro/01-what-is-ml.md",
            source_commit_sha="a" * 40,
            source_checksum="b" * 64,
            content_markdown="",
        )

    def unit_body(self, markdown: str, *, title: str | None = None) -> str:
        self.unit.content_markdown = markdown
        fields = ["content_markdown"]
        if title is not None:
            self.unit.title = title
            fields.append("title")
        self.unit.save(update_fields=fields)
        url = reverse(
            "unit",
            kwargs={
                "course_slug": self.course_family.slug,
                "cohort_identifier": self.cohort.identifier,
                "module_slug": self.module.slug,
                "unit_slug": self.unit.slug,
            },
        )
        body = self.client.get(url).content.decode()
        return body[
            body.index('<article class="prose prose-reading unit-content">') : body.index(
                "</article>"
            )
        ]

    def test_a_leading_h2_that_is_the_title_is_removed(self):
        """This is the shape 103 imported ML lessons are written in."""

        article = self.unit_body(
            "## 1.1 Introduction to Machine Learning\n\nThe lesson body.",
        )

        self.assertNotIn("1.1 Introduction to Machine Learning", article)
        self.assertIn("The lesson body.", article)

    def test_a_leading_h1_that_is_the_title_is_still_removed(self):
        article = self.unit_body(
            "# Introduction\n\nThe lesson body.",
            title="Introduction",
        )

        self.assertNotIn("<h1", article)
        self.assertIn("The lesson body.", article)

    def test_a_leading_heading_that_is_not_the_title_is_kept(self):
        """The ai-dev-tools lessons open with a heading that is not their title."""

        article = self.unit_body(
            "## Prerequisites\n\nThe lesson body.",
        )

        self.assertIn("<h2>Prerequisites</h2>", article)
        self.assertIn("The lesson body.", article)

    def test_a_matching_heading_below_the_first_line_is_kept(self):
        article = self.unit_body(
            "An opening paragraph.\n\n## 1.1 Introduction to Machine Learning\n\nMore.",
        )

        self.assertIn("1.1 Introduction to Machine Learning", article)

    def test_a_deeper_heading_is_never_treated_as_the_document_title(self):
        article = self.unit_body(
            "### 1.1 Introduction to Machine Learning\n\nThe lesson body.",
        )

        self.assertIn("1.1 Introduction to Machine Learning", article)

    def test_a_body_that_was_only_its_title_renders_no_heading_at_all(self):
        article = self.unit_body("## 1.1 Introduction to Machine Learning\n")

        self.assertNotIn("<h1", article)
        self.assertNotIn("<h2", article)
