from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from courses.models import (
    Cohort,
    Course,
    CurriculumFormat,
    Homework,
    Module,
    Unit,
    UnitReadState,
)
from courses.services.unit_read_state import set_unit_read_state


class ModulePageTests(TestCase):
    def setUp(self):
        self.course = Course.objects.create(
            slug="llm-zoomcamp",
            title="LLM Zoomcamp",
        )
        self.cohort = Cohort.objects.create(
            course=self.course,
            identifier="2026",
            slug="llm-zoomcamp-2026",
            title="LLM Zoomcamp 2026",
            description="A module-format cohort.",
            curriculum_format=CurriculumFormat.MODULES,
        )
        self.homework = Homework.objects.create(
            course=self.cohort,
            slug="homework-01",
            title="Homework 1",
            due_date=timezone.now() + timedelta(days=7),
        )
        self.module = Module.objects.create(
            cohort=self.cohort,
            position=10,
            slug="01-agentic-rag",
            title="Module 1: Agentic RAG",
            terminal_homework=self.homework,
        )
        self.units = [
            Unit.objects.create(
                module=self.module,
                position=position,
                slug=slug,
                title=title,
            )
            for position, slug, title in (
                (10, "01-intro", "Introduction"),
                (20, "02-rag", "RAG"),
                (30, "03-dataset", "The Course FAQ Dataset"),
            )
        ]

    def module_url(self, **overrides):
        kwargs = {
            "course_slug": self.course.slug,
            "cohort_identifier": self.cohort.identifier,
            "module_slug": self.module.slug,
        }
        kwargs.update(overrides)
        return reverse("module", kwargs=kwargs)

    def read_state_url(self, unit=None, **overrides):
        unit = unit or self.units[0]
        kwargs = {
            "course_slug": self.course.slug,
            "cohort_identifier": self.cohort.identifier,
            "module_slug": self.module.slug,
            "unit_slug": unit.slug,
        }
        kwargs.update(overrides)
        return reverse("unit_read_state", kwargs=kwargs)

    def test_module_page_lists_units_homework_and_side_panel(self):
        response = self.client.get(self.module_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.module.title)
        self.assertContains(response, self.homework.title)
        self.assertContains(response, "Lessons")
        # The rail states sign-in once, as a quiet link, and no longer opens
        # with a preamble sentence above the list.
        self.assertNotContains(response, "Sign in to keep track of what you have read.")
        self.assertContains(response, "Sign in to track progress")
        self.assertContains(response, 'class="module-sidebar module-rail"')
        for unit in self.units:
            self.assertContains(response, unit.title)
            self.assertContains(
                response,
                reverse(
                    "unit",
                    kwargs={
                        "course_slug": self.course.slug,
                        "cohort_identifier": self.cohort.identifier,
                        "module_slug": self.module.slug,
                        "unit_slug": unit.slug,
                    },
                ),
            )

        body = response.content.decode()
        self.assertIn('class="module-layout shell-breakout"', body)
        self.assertLess(body.index('class="module-main"'), body.index('class="module-sidebar'))
        self.assertNotIn("Shared module navigation contract", body)

    def test_breadcrumb_stops_at_the_edition_and_names_it_once(self):
        """Ancestors only, and the cohort crumb is the identifier, not the course again."""

        body = self.client.get(self.module_url()).content.decode()
        trail = body.split('<nav class="breadcrumbs"', 1)[1].split("</nav>", 1)[0]

        self.assertIn(">Courses</a>", trail)
        self.assertIn(f">{self.course.title}</a>", trail)
        self.assertIn(f">{self.cohort.identifier}</a>", trail)
        self.assertNotIn(self.cohort.title, trail)
        # This module is the heading below, so it is not also a crumb.
        self.assertNotIn(self.module.title, trail)
        self.assertNotIn('aria-current="page"', trail)
        self.assertEqual(trail.count("<li"), 3)

    def test_module_and_unit_links_keep_numeric_source_slugs(self):
        module_url = reverse(
            "module",
            kwargs={
                "course_slug": self.course.slug,
                "cohort_identifier": self.cohort.identifier,
                "module_slug": "01-agentic-rag",
            },
        )
        unit_url = reverse(
            "unit",
            kwargs={
                "course_slug": self.course.slug,
                "cohort_identifier": self.cohort.identifier,
                "module_slug": "01-agentic-rag",
                "unit_slug": "01-intro",
            },
        )

        self.assertEqual(module_url, "/courses/llm-zoomcamp/2026/modules/01-agentic-rag")
        self.assertEqual(
            unit_url,
            "/courses/llm-zoomcamp/2026/modules/01-agentic-rag/01-intro",
        )
        response = self.client.get(module_url)
        self.assertContains(response, unit_url)

    def test_authenticated_read_state_is_idempotent_and_rendered_in_sidebar(self):
        user = get_user_model().objects.create_user(username="learner")
        self.client.force_login(user)

        response = self.client.post(self.read_state_url(self.units[0]), {"is_read": "1"})
        self.assertRedirects(response, self.module_url(), fetch_redirect_response=False)
        self.assertEqual(
            UnitReadState.objects.filter(user=user, unit=self.units[0]).count(),
            1,
        )

        response = self.client.post(self.read_state_url(self.units[0]), {"is_read": "1"})
        self.assertRedirects(response, self.module_url(), fetch_redirect_response=False)
        self.assertEqual(
            UnitReadState.objects.filter(user=user, unit=self.units[0]).count(),
            1,
        )
        page = self.client.get(self.module_url())
        self.assertContains(page, "1 of 3 lessons read")
        self.assertContains(page, "Read")
        # The rail shows read state; it never edits it.  The single toggle lives
        # at the foot of the lesson the reader has just finished.
        self.assertNotContains(page, "Mark as unread")

        response = self.client.post(self.read_state_url(self.units[0]), {"is_read": "0"})
        self.assertRedirects(response, self.module_url(), fetch_redirect_response=False)
        response = self.client.post(self.read_state_url(self.units[0]), {"is_read": "0"})
        self.assertRedirects(response, self.module_url(), fetch_redirect_response=False)
        self.assertFalse(UnitReadState.objects.filter(user=user, unit=self.units[0]).exists())
        page = self.client.get(self.module_url())
        self.assertContains(page, "0 of 3 lessons read")
        self.assertNotContains(page, "Mark as read")

    def test_anonymous_read_state_update_requires_authentication(self):
        response = self.client.post(self.read_state_url(), {"is_read": "1"})

        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])
        self.assertFalse(UnitReadState.objects.exists())

    def test_invalid_state_and_targets_are_rejected(self):
        user = get_user_model().objects.create_user(username="learner")
        self.client.force_login(user)

        response = self.client.post(self.read_state_url(), {"is_read": "yes"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(UnitReadState.objects.exists())
        self.assertEqual(
            self.client.get(self.module_url(module_slug="missing-module")).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                self.read_state_url(unit_slug="missing-unit"),
                {"is_read": "1"},
            ).status_code,
            404,
        )

    def test_legacy_cohorts_cannot_use_module_routes_or_read_state(self):
        legacy = Cohort.objects.create(
            course=self.course,
            identifier="legacy",
            slug="llm-zoomcamp-legacy",
            year=2025,
            title="LLM Zoomcamp legacy",
            description="Legacy cohort.",
            curriculum_format=CurriculumFormat.LEGACY,
        )
        legacy_homework = Homework.objects.create(
            course=legacy,
            slug="legacy-homework",
            title="Legacy homework",
            due_date=timezone.now() + timedelta(days=7),
        )
        legacy_module = Module.objects.create(
            cohort=legacy,
            position=10,
            slug="legacy-module",
            title="Legacy module",
            terminal_homework=legacy_homework,
        )
        legacy_unit = Unit.objects.create(
            module=legacy_module,
            position=10,
            slug="legacy-unit",
            title="Legacy unit",
        )
        user = get_user_model().objects.create_user(username="legacy-learner")
        self.client.force_login(user)

        module_url = self.module_url(
            cohort_identifier=legacy.identifier,
            module_slug=legacy_module.slug,
        )
        read_url = self.read_state_url(
            unit=legacy_unit,
            cohort_identifier=legacy.identifier,
            module_slug=legacy_module.slug,
        )
        self.assertEqual(self.client.get(module_url).status_code, 404)
        self.assertEqual(self.client.post(read_url, {"is_read": "1"}).status_code, 404)
        self.assertFalse(UnitReadState.objects.exists())

    def test_service_rejects_a_unit_from_another_module(self):
        user = get_user_model().objects.create_user(username="learner")
        other_homework = Homework.objects.create(
            course=self.cohort,
            slug="homework-02",
            title="Homework 2",
            due_date=timezone.now() + timedelta(days=7),
        )
        other_module = Module.objects.create(
            cohort=self.cohort,
            position=20,
            slug="other-module",
            title="Other module",
            terminal_homework=other_homework,
        )
        other_unit = Unit.objects.create(
            module=other_module,
            position=10,
            slug="other-unit",
            title="Other unit",
        )

        with self.assertRaises(ValidationError):
            set_unit_read_state(
                user=user,
                module=self.module,
                unit=other_unit,
                is_read=True,
            )
