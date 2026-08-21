from pathlib import Path

from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from courses.models import Cohort, Course, CurriculumFormat, Homework, Module, Unit
from courses.registration import markdown_has_mermaid, render_markdown


class CourseMermaidMarkdownTests(SimpleTestCase):
    def test_mermaid_fence_is_an_escaped_diagram_div(self):
        rendered = render_markdown(
            '```mermaid\nflowchart LR\n    A["<script>alert(1)</script>"] --> B\n```'
        )

        self.assertIn('<div class="mermaid">', rendered)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", rendered)
        self.assertNotIn("<pre>", rendered)
        self.assertTrue(markdown_has_mermaid(rendered))

    def test_regular_fences_stay_code_blocks_and_mermaid_detection_is_exact(self):
        rendered = render_markdown("```python\nprint('hello')\n```")

        self.assertIn("<pre><code>print('hello')\n</code></pre>", rendered)
        self.assertFalse(markdown_has_mermaid(rendered))


class CourseMermaidRuntimeTests(SimpleTestCase):
    repository_root = Path(__file__).resolve().parents[2]

    def test_runtime_is_csp_safe_and_theme_aware(self):
        runtime = (
            self.repository_root / "courses/static/courses/mermaid_render.js"
        ).read_text(encoding="utf-8")

        for marker in (
            "import(moduleUrl.href)",
            "querySelectorAll(\"div.mermaid\")",
            "securityLevel: \"strict\"",
            "themeVariables",
            "dark-mode",
            "MutationObserver",
            "mermaid.run({ nodes: nodes })",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, runtime)
        self.assertNotIn("eval(", runtime)
        self.assertNotIn("new Function", runtime)

    def test_mermaid_template_uses_local_module_and_overflow_rules(self):
        template = (
            self.repository_root / "courses/templates/courses/_mermaid.html"
        ).read_text(encoding="utf-8")

        self.assertIn("courses/mermaid_render.js", template)
        self.assertIn("overflow-x: auto", template)
        self.assertIn("max-width: none", template)


class PublicCourseMermaidPageTests(TestCase):
    def setUp(self):
        course = Course.objects.create(slug="mermaid-course", title="Mermaid Course")
        cohort = Cohort.objects.create(
            course=course,
            identifier="2026",
            year=2026,
            title="Mermaid Course 2026",
            description="Mermaid fixture.",
            curriculum_format=CurriculumFormat.MODULES,
        )
        homework = Homework.objects.create(
            course=cohort,
            slug="homework",
            title="Homework",
            due_date=timezone.now(),
        )
        module = Module.objects.create(
            cohort=cohort,
            position=10,
            slug="module",
            title="Module",
            terminal_homework=homework,
        )
        self.unit = Unit.objects.create(
            module=module,
            position=10,
            slug="diagram",
            title="Diagram",
            content_markdown="```mermaid\nflowchart LR\n    A --> B\n```",
        )

    def test_unit_page_includes_mermaid_runtime_only_for_unit_surface(self):
        response = self.client.get(
            reverse(
                "unit",
                kwargs={
                    "course_slug": "mermaid-course",
                    "cohort_identifier": "2026",
                    "module_slug": "module",
                    "unit_slug": "diagram",
                },
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<div class="mermaid">flowchart LR')
        self.assertContains(response, 'src="/static/courses/mermaid_render.js"')
        self.assertContains(response, "overflow-x: auto")
        self.assertNotContains(response, "mermaid.esm.min.mjs")

    def test_homepage_does_not_include_mermaid_loader(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "courses/mermaid_render.js")
