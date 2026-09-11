"""Delivery context resolution and shared course route tests (W4)."""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from courses.models import (
    Cohort,
    CohortSharedModule,
    Course,
    DeliveryMode,
    Enrollment,
    Homework,
    SharedCurriculum,
    SharedLesson,
    SharedModule,
)
from courses.services.course_context import (
    resolve_delivery_context,
    context_query,
)

SHA = "a" * 40
CHECKSUM = "b" * 64


def provenance(content_id: str, path: str) -> dict[str, str]:
    return {
        "source_content_id": content_id,
        "source_path": path,
        "source_commit_sha": SHA,
        "source_checksum": CHECKSUM,
    }


class SharedWorldTestCase(TestCase):
    """One course with a shared graph and two cohorts (2026 + self-paced)."""

    def setUp(self) -> None:
        super().setUp()
        self.course = Course.objects.create(slug="llm-zoomcamp", title="LLM Zoomcamp")
        self.shared = SharedCurriculum.objects.create(
            course=self.course,
            parser_version="course-repository-v2",
            **provenance("11111111-1111-4111-8111-111111111111", "course.yaml"),
        )
        self.module = SharedModule.objects.create(
            curriculum=self.shared,
            position=0,
            slug="01-agentic-rag",
            title="Agentic RAG",
            **provenance("22222222-2222-4222-8222-222222222222", "01-agentic-rag/module.yaml"),
        )
        self.lesson = SharedLesson.objects.create(
            module=self.module,
            position=0,
            slug="01-lesson",
            title="Introduction",
            content_markdown="# Introduction\n",
            rendered_html="<h2>Introduction</h2>\n",
            **provenance("33333333-3333-4333-8333-333333333333", "01-agentic-rag/01-lesson.md"),
        )
        self.cohort_2026 = Cohort.objects.create(
            course=self.course,
            slug="llm-zoomcamp-2026",
            title="LLM Zoomcamp 2026",
            description="live",
            identifier="2026",
            year=2026,
            curriculum_format="shared",
            delivery_mode=DeliveryMode.LIVE,
        )
        self.cohort_2026.shared_curriculum = self.shared
        self.cohort_2026.save()
        self.homework = Homework.objects.create(
            course=self.cohort_2026,
            slug="hw1",
            title="Homework 1",
            due_date=timezone.now() + timedelta(days=7),
        )
        CohortSharedModule.objects.create(
            cohort=self.cohort_2026,
            shared_module=self.module,
            position=0,
            terminal_homework=self.homework,
        )
        self.cohort_self_paced = Cohort.objects.create(
            course=self.course,
            slug="llm-zoomcamp-self-paced",
            title="LLM Zoomcamp Self-Paced",
            description="self paced",
            identifier="self-paced",
            year=2027,
            curriculum_format="shared",
            delivery_mode=DeliveryMode.SELF_PACED,
        )
        self.cohort_self_paced.shared_curriculum = self.shared
        self.cohort_self_paced.save()

    def module_url(self) -> str:
        return reverse(
            "shared_module",
            kwargs={"course_slug": self.course.slug, "module_slug": self.module.slug},
        )

    def lesson_url(self) -> str:
        return reverse(
            "shared_lesson",
            kwargs={
                "course_slug": self.course.slug,
                "module_slug": self.module.slug,
                "lesson_slug": self.lesson.slug,
            },
        )


class DeliveryContextTests(SharedWorldTestCase):
    def test_explicit_query_resolves_and_beats_everything(self) -> None:
        request = self.client.get(self.module_url() + "?cohort=self-paced").wsgi_request
        context = resolve_delivery_context(request, self.course)

        self.assertEqual(context.source, "explicit")
        self.assertEqual(context.cohort.pk, self.cohort_self_paced.pk)
        self.assertTrue(context.explicit)
        self.assertTrue(context.varies_response)

    def test_duplicate_query_value_is_invalid_without_fallback(self) -> None:
        url = self.module_url() + "?cohort=2026&cohort=self-paced"
        request = self.client.get(url).wsgi_request
        context = resolve_delivery_context(request, self.course)

        self.assertEqual(context.source, "invalid")
        self.assertIsNone(context.cohort)

    def test_malformed_query_value_is_invalid(self) -> None:
        request = self.client.get(self.module_url() + "?cohort=2026!").wsgi_request
        context = resolve_delivery_context(request, self.course)

        self.assertEqual(context.source, "invalid")
        self.assertIsNotNone(context.invalid_identifier)

    def test_unknown_explicit_identifier_never_falls_back(self) -> None:
        request = self.client.get(self.module_url() + "?cohort=2099").wsgi_request
        context = resolve_delivery_context(request, self.course)

        self.assertEqual(context.source, "invalid")
        self.assertIsNone(context.cohort)

    def test_sole_enrollment_is_used_without_query(self) -> None:
        user = Enrollment.objects.create(
            student=get_user_model().objects.create_user(username="solo"),
            course=self.cohort_2026,
        ).student
        request = self.client.get(self.module_url()).wsgi_request
        request.user = user
        context = resolve_delivery_context(request, self.course)

        self.assertEqual(context.source, "sole_enrollment")
        self.assertEqual(context.cohort.pk, self.cohort_2026.pk)
        self.assertIsNotNone(context.enrollment)

    def test_multiple_enrollments_choose_not_newest(self) -> None:
        user = get_user_model().objects.create_user(username="multi")
        Enrollment.objects.create(student=user, course=self.cohort_2026)
        Enrollment.objects.create(student=user, course=self.cohort_self_paced)
        request = self.client.get(self.module_url()).wsgi_request
        request.user = user
        context = resolve_delivery_context(request, self.course)

        self.assertEqual(context.source, "chooser")
        self.assertIsNone(context.cohort)
        self.assertTrue(context.has_multiple_enrollments)

    def test_anonymous_chooser_context_stays_cache_eligible(self) -> None:
        request = self.client.get(self.module_url()).wsgi_request
        context = resolve_delivery_context(request, self.course)

        self.assertEqual(context.source, "chooser")
        self.assertFalse(context.varies_response)

    def test_context_query_helper(self) -> None:
        self.assertEqual(context_query(self.cohort_2026), "?cohort=2026")
        self.assertEqual(context_query(None), "")


class SharedRouteTests(SharedWorldTestCase):
    def test_shared_module_page_renders_anonymously(self) -> None:
        response = self.client.get(self.module_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Agentic RAG")
        self.assertContains(response, self.lesson.title)
        self.assertNotIn("cohort=", response.get("Cache-Control", ""))

    def test_shared_lesson_page_renders_and_links_siblings(self) -> None:
        SharedLesson.objects.create(
            module=self.module,
            position=1,
            slug="02-practice",
            title="Practice",
            rendered_html="<h2>Practice</h2>",
            **provenance("34444444-4444-4444-8444-444444444444", "01-agentic-rag/02-practice.md"),
        )
        response = self.client.get(self.lesson_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Practice")

    def test_two_segment_cohort_path_redirects_to_the_canonical_namespace(self) -> None:
        legacy = f"/courses/{self.course.slug}/{self.cohort_2026.identifier}"
        response = self.client.get(legacy)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse(
                "cohort",
                kwargs={
                    "course_slug": self.course.slug,
                    "cohort_identifier": self.cohort_2026.identifier,
                },
            ),
        )

    def test_canonical_cohort_page_renders(self) -> None:
        response = self.client.get(
            reverse(
                "cohort",
                kwargs={
                    "course_slug": self.course.slug,
                    "cohort_identifier": self.cohort_2026.identifier,
                },
            )
        )

        self.assertEqual(response.status_code, 200)

    def test_canonical_cohort_page_renders_its_shared_modules(self) -> None:
        """A shared-format cohort's placements render on its own landing page.

        Regression test: the cohort page used to only understand the
        modules-format curriculum flow, so a shared-format cohort with real
        ``CohortSharedModule`` placements silently fell back to the legacy
        homework-table rendering and showed no module at all.
        """

        response = self.client.get(
            reverse(
                "cohort",
                kwargs={
                    "course_slug": self.course.slug,
                    "cohort_identifier": self.cohort_2026.identifier,
                },
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["is_module_curriculum"])
        flow = response.context["curriculum_flow"]
        self.assertEqual(len(flow), 1)
        self.assertEqual(flow[0].kind, "module")
        self.assertEqual(flow[0].module.pk, self.module.pk)
        self.assertIs(flow[0].homework, response.context["homeworks"][0])
        self.assertContains(response, self.module.title)
        self.assertContains(
            response,
            reverse(
                "shared_module",
                kwargs={"course_slug": self.course.slug, "module_slug": self.module.slug},
            ),
        )

    def test_unknown_cohort_identifier_is_a_real_404(self) -> None:
        response = self.client.get(
            reverse(
                "cohort",
                kwargs={"course_slug": self.course.slug, "cohort_identifier": "2099"},
            )
        )

        self.assertEqual(response.status_code, 404)

    def test_unknown_module_and_unknown_identifier_are_404(self) -> None:
        self.assertEqual(
            self.client.get(
                f"/courses/{self.course.slug}/99-not-a-module"
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(f"/courses/{self.course.slug}/2026/01-lesson").status_code,
            404,
        )

    def test_explicit_context_marks_response_private(self) -> None:
        response = self.client.get(self.lesson_url() + "?cohort=2026")

        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn("private", response["Cache-Control"])

    def test_clean_anonymous_lesson_stays_public_cache_eligible(self) -> None:
        response = self.client.get(self.lesson_url())

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("no-store", response.get("Cache-Control", ""))

    def test_self_paced_panel_promises_no_deadlines(self) -> None:
        response = self.client.get(self.lesson_url() + "?cohort=self-paced")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "self-paced")
        self.assertNotContains(response, "Homework 1")

    def test_authenticated_read_never_creates_an_enrollment(self) -> None:
        user = get_user_model().objects.create_user(username="window-shopper")
        self.client.force_login(user)
        url = reverse(
            "cohort_homework",
            kwargs={
                "course_slug": self.course.slug,
                "cohort_identifier": self.cohort_2026.identifier,
                "homework_slug": self.homework.slug,
            },
        )

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            Enrollment.objects.filter(student=user, course=self.cohort_2026).exists()
        )

    def test_copied_url_for_a_different_cohort_grants_nothing(self) -> None:
        user = get_user_model().objects.create_user(username="copied-link")
        Enrollment.objects.create(student=user, course=self.cohort_self_paced)
        self.client.force_login(user)
        url = reverse(
            "cohort_homework",
            kwargs={
                "course_slug": self.course.slug,
                "cohort_identifier": self.cohort_2026.identifier,
                "homework_slug": self.homework.slug,
            },
        )

        response = self.client.get(url)

        # The read renders (public assignment statements are readable), but no
        # enrollment in the path's cohort may appear as a side effect.
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            Enrollment.objects.filter(student=user, course=self.cohort_2026).exists()
        )
