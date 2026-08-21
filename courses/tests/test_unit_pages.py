import uuid
from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import resolve, reverse
from django.utils import timezone

from courses.models import Cohort, Course, CurriculumFormat, Homework, Module, Unit


class PublicUnitPageTests(TestCase):
    def setUp(self):
        self.course_family = Course.objects.create(
            slug="llm-zoomcamp",
            title="LLM Zoomcamp",
        )
        self.cohort = Cohort.objects.create(
            course=self.course_family,
            slug="llm-zoomcamp-spring-2026",
            identifier="spring-2026",
            year=2026,
            title="LLM Zoomcamp Spring 2026",
            description="A module-format cohort.",
            curriculum_format=CurriculumFormat.MODULES,
            github_repo_url="https://github.com/DataTalksClub/llm-zoomcamp.git",
        )
        self.homework = Homework.objects.create(
            course=self.cohort,
            slug="agentic-rag-homework",
            title="Agentic RAG Homework",
            due_date=timezone.now() + timedelta(days=7),
        )
        self.module = Module.objects.create(
            cohort=self.cohort,
            position=10,
            slug="01-agentic-rag",
            title="Agentic RAG",
            terminal_homework=self.homework,
        )
        self.first_unit = Unit.objects.create(
            module=self.module,
            position=10,
            slug="01-intro",
            title="Introduction",
            source_content_id=uuid.uuid4(),
            source_path="cohorts/2026/01-agentic-rag/lessons/01-intro.md",
            source_commit_sha="a" * 40,
            source_checksum="b" * 64,
            content_markdown=(
                "## Welcome\n\nBuild an **agent** with `Python`.\n\n"
                "```python\nprint(\"hello, agent\")\n```"
            ),
        )
        self.middle_unit = Unit.objects.create(
            module=self.module,
            position=20,
            slug="02-environment",
            title="Environment",
            content_markdown="Configure the environment.",
        )
        self.final_unit = Unit.objects.create(
            module=self.module,
            position=30,
            slug="03-evaluation",
            title="Evaluation",
            content_markdown="Evaluate the system.",
        )

    def unit_url(self, unit, **overrides):
        kwargs = {
            "course_slug": self.course_family.slug,
            "cohort_identifier": self.cohort.identifier,
            "module_slug": self.module.slug,
            "unit_slug": unit.slug,
        }
        kwargs.update(overrides)
        return reverse("unit", kwargs=kwargs)

    def test_renders_markdown_and_course_hierarchy(self):
        url = self.unit_url(self.first_unit)

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(resolve(url).url_name, "unit")
        self.assertEqual(response.context["unit"], self.first_unit)
        self.assertContains(response, "<h2>Welcome</h2>", html=True)
        self.assertContains(response, "<strong>agent</strong>", html=True)
        self.assertContains(response, "<code>Python</code>", html=True)
        self.assertContains(
            response,
            '<pre><code>print("hello, agent")\n</code></pre>',
            html=True,
        )
        self.assertContains(response, 'src="/static/core/code_blocks.js"')
        self.assertContains(response, self.course_family.title)
        self.assertContains(response, self.cohort.title)
        self.assertContains(response, self.module.title)
        self.assertContains(response, 'class="module-sidebar module-rail"')
        self.assertContains(response, self.middle_unit.title)
        self.assertContains(response, 'aria-current="page"')
        self.assertContains(
            response,
            f'<link rel="canonical" href="https://datatalks.club{url}">',
            html=True,
        )
        self.assertContains(response, 'class="band band-lavender content-page-content')
        self.assertContains(response, 'class="module-layout"')

    def test_middle_unit_links_to_previous_and_next_units_with_buttons(self):
        response = self.client.get(self.unit_url(self.middle_unit))

        self.assertContains(response, self.unit_url(self.first_unit))
        self.assertContains(response, self.unit_url(self.final_unit))
        self.assertContains(response, "← Introduction")
        self.assertContains(response, "Evaluation →")
        self.assertNotContains(response, "Continue to homework")

    def test_video_link_is_embedded_and_redundant_markdown_title_is_removed(self):
        self.first_unit.content_markdown = (
            "# Introduction\n\n"
            "Video: [Watch this lesson](https://www.youtube.com/watch?v=abc123)\n\n"
            "The lesson body."
        )
        self.first_unit.save(update_fields=["content_markdown"])

        response = self.client.get(self.unit_url(self.first_unit))

        self.assertContains(response, 'src="https://www.youtube.com/embed/abc123"')
        self.assertContains(response, 'title="Introduction video lesson"')
        self.assertContains(response, 'referrerpolicy="strict-origin-when-cross-origin"')
        self.assertContains(response, "The lesson body.")
        self.assertNotContains(response, "Watch this lesson")
        self.assertNotContains(response, "<h1>Introduction</h1>", html=True)

    def test_renders_edit_on_github_link_from_cohort_repository_and_source_path(self):
        response = self.client.get(self.unit_url(self.first_unit))

        edit_url = (
            "https://github.com/DataTalksClub/llm-zoomcamp/edit/main/"
            "cohorts/2026/01-agentic-rag/lessons/01-intro.md"
        )
        self.assertContains(response, "Edit on GitHub")
        self.assertContains(
            response,
            edit_url,
        )
        self.assertContains(response, 'target="_blank"')

    def test_edit_link_falls_back_to_course_family_repository(self):
        self.cohort.github_repo_url = ""
        self.cohort.save(update_fields=["github_repo_url"])
        self.course_family.github_repo_url = "https://github.com/example/llm-zoomcamp/"
        self.course_family.save(update_fields=["github_repo_url"])

        response = self.client.get(self.unit_url(self.first_unit))

        self.assertContains(
            response,
            "https://github.com/example/llm-zoomcamp/edit/main/"
            "cohorts/2026/01-agentic-rag/lessons/01-intro.md",
        )

    def test_does_not_render_edit_link_without_repository_or_source_path(self):
        self.cohort.github_repo_url = ""
        self.cohort.save(update_fields=["github_repo_url"])
        self.first_unit.source_content_id = None
        self.first_unit.source_path = None
        self.first_unit.source_commit_sha = None
        self.first_unit.source_checksum = None
        self.first_unit.save(
            update_fields=[
                "source_content_id",
                "source_path",
                "source_commit_sha",
                "source_checksum",
            ]
        )

        response = self.client.get(self.unit_url(self.first_unit))

        self.assertNotContains(response, "Edit on GitHub")

    def test_final_unit_links_to_homework_with_a_button(self):
        response = self.client.get(self.unit_url(self.final_unit))
        homework_url = reverse(
            "homework",
            kwargs={
                "course_slug": self.course_family.slug,
                "cohort_year": self.cohort.identifier,
                "homework_slug": self.homework.slug,
            },
        )

        self.assertContains(response, homework_url)
        self.assertContains(response, "Continue to homework →")

    def test_arbitrary_cohort_identifier_is_the_route_identity(self):
        url = self.unit_url(self.first_unit)

        self.assertEqual(
            url,
            "/courses/llm-zoomcamp/spring-2026/modules/01-agentic-rag/01-intro",
        )
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_sanitizes_active_content_from_markdown(self):
        self.first_unit.content_markdown = (
            "Safe text.\n\n"
            "<script>alert('xss')</script>\n\n"
            '<img src="x" onerror="alert(1)">\n\n'
            "[unsafe](javascript:alert(1))"
        )
        self.first_unit.save(update_fields=["content_markdown"])

        response = self.client.get(self.unit_url(self.first_unit))
        body = response.content.decode()
        article = body[
            body.index('<article class="prose prose-reading unit-content">') : body.index(
                "</article>"
            )
        ]

        self.assertContains(response, "Safe text.")
        self.assertNotIn("<script", article)
        self.assertNotIn("<img", article)
        self.assertNotIn('href="javascript:', article)
        self.assertIn("&lt;script&gt;", article)

    def test_returns_404_for_legacy_or_mismatched_ownership(self):
        legacy_cohort = Cohort.objects.create(
            course=self.course_family,
            slug="llm-zoomcamp-legacy",
            identifier="legacy",
            year=2025,
            title="Legacy cohort",
            description="Legacy curriculum.",
            curriculum_format=CurriculumFormat.LEGACY,
        )
        legacy_homework = Homework.objects.create(
            course=legacy_cohort,
            slug="legacy-homework",
            title="Legacy Homework",
            due_date=timezone.now() + timedelta(days=7),
        )
        legacy_module = Module.objects.create(
            cohort=legacy_cohort,
            position=10,
            slug="legacy-module",
            title="Legacy Module",
            terminal_homework=legacy_homework,
        )
        legacy_unit = Unit.objects.create(
            module=legacy_module,
            position=10,
            slug="legacy-unit",
            title="Legacy Unit",
        )
        other_family = Course.objects.create(
            slug="other-course",
            title="Other Course",
        )
        Cohort.objects.create(
            course=other_family,
            slug="other-course-spring-2026",
            identifier=self.cohort.identifier,
            year=2026,
            title="Other Course Spring 2026",
            description="Another module-format cohort.",
            curriculum_format=CurriculumFormat.MODULES,
        )
        other_homework = Homework.objects.create(
            course=self.cohort,
            slug="other-homework",
            title="Other Homework",
            due_date=timezone.now() + timedelta(days=7),
        )
        other_module = Module.objects.create(
            cohort=self.cohort,
            position=20,
            slug="other-module",
            title="Other Module",
            terminal_homework=other_homework,
        )
        other_unit = Unit.objects.create(
            module=other_module,
            position=10,
            slug="other-unit",
            title="Other Unit",
        )

        cases = (
            self.unit_url(
                legacy_unit,
                cohort_identifier=legacy_cohort.identifier,
                module_slug=legacy_module.slug,
            ),
            self.unit_url(self.first_unit, course_slug=other_family.slug),
            self.unit_url(other_unit, module_slug=self.module.slug),
            self.unit_url(self.first_unit, module_slug="missing-module"),
            self.unit_url(self.first_unit, unit_slug="missing-unit"),
        )
        for url in cases:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)

    @override_settings(NOINDEX=False)
    def test_uses_public_course_page_index_and_cache_policy(self):
        response = self.client.get(self.unit_url(self.first_unit))

        self.assertNotIn("X-Robots-Tag", response.headers)
        cache_control = response.headers.get("Cache-Control", "")
        self.assertNotIn("private", cache_control)
        self.assertNotIn("no-store", cache_control)
