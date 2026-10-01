"""D5.3a route and neighbor policy for authored course source identities."""

from __future__ import annotations

from django.urls import reverse

from courses.models import SharedLesson, SharedModule
from courses.tests.test_shared_course_routes import SharedWorldTestCase, provenance


class SourcePolicyRouteTests(SharedWorldTestCase):
    def _add_practice(self) -> SharedLesson:
        return SharedLesson.objects.create(
            module=self.module,
            position=1,
            slug="02-practice",
            title="Practice",
            rendered_html="<p>Practice</p>",
            **provenance(
                "34444444-4444-4444-8444-444444444444",
                "01-agentic-rag/02-practice.md",
            ),
        )

    def _add_other_module(self) -> None:
        module = SharedModule.objects.create(
            curriculum=self.shared,
            position=1,
            slug="02-evaluation",
            title="Evaluation",
            **provenance("81111111-1111-4111-8111-111111111111", "02-evaluation/module.yaml"),
        )
        SharedLesson.objects.create(
            module=module,
            position=0,
            slug="01-evaluate",
            title="Evaluate",
            rendered_html="<p>Evaluate</p>",
            **provenance("82222222-2222-4222-8222-222222222222", "02-evaluation/01-evaluate.md"),
        )

    def test_authored_numeric_slugs_keep_the_canonical_routes(self) -> None:
        practice = self._add_practice()
        first_path = "/courses/llm-zoomcamp/01-agentic-rag/01-lesson"
        second_path = "/courses/llm-zoomcamp/01-agentic-rag/02-practice"
        self.assertEqual(self.lesson_url(), first_path)
        self.assertEqual(
            reverse(
                "shared_lesson",
                kwargs={
                    "course_slug": self.course.slug,
                    "module_slug": self.module.slug,
                    "lesson_slug": practice.slug,
                },
            ),
            second_path,
        )

    def test_neighbors_stay_within_the_module_and_keep_cohort_context(self) -> None:
        self._add_practice()
        self._add_other_module()
        first_path = "/courses/llm-zoomcamp/01-agentic-rag/01-lesson"
        second_path = "/courses/llm-zoomcamp/01-agentic-rag/02-practice"
        first = self.client.get(first_path + "?cohort=2026")
        second = self.client.get(second_path + "?cohort=2026")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.context["previous_lesson_url"], "")
        self.assertEqual(first.context["next_lesson_url"], second_path + "?cohort=2026")
        self.assertEqual(second.context["previous_lesson_url"], first_path + "?cohort=2026")
        self.assertEqual(second.context["next_lesson_url"], "")
