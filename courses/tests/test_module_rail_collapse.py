"""The lesson page's hide/show control for the module rail.

The browser test drives the toggle itself.  These checks own the contract the
markup has to hold before any script runs: that the lesson page ships the
pre-paint bootstrap, that the two halves of the control carry a name, a state
and a target, that the state lives in the browser rather than in the response
(so an anonymous lesson stays byte-identical and cacheable), and that the module
index -- where the rail is the page's own subject rather than chrome beside a
long read -- is left alone.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from courses.models import Cohort, Course, CurriculumFormat, Homework, Module, Unit


class ModuleRailCollapseTests(TestCase):
    def setUp(self):
        self.course_family = Course.objects.create(
            slug="llm-zoomcamp",
            title="LLM Zoomcamp",
        )
        self.cohort = Cohort.objects.create(
            course=self.course_family,
            slug="llm-zoomcamp-2026",
            identifier="2026",
            year=2026,
            title="LLM Zoomcamp 2026",
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
        self.unit = Unit.objects.create(
            module=self.module,
            position=10,
            slug="01-intro",
            title="Introduction",
            content_markdown="The first lesson.",
        )
        Unit.objects.create(
            module=self.module,
            position=20,
            slug="02-environment",
            title="Environment",
            content_markdown="The second lesson.",
        )

    def unit_url(self):
        return reverse(
            "unit",
            kwargs={
                "course_slug": self.course_family.slug,
                "cohort_identifier": self.cohort.identifier,
                "module_slug": self.module.slug,
                "unit_slug": self.unit.slug,
            },
        )

    def module_url(self):
        return reverse(
            "module",
            kwargs={
                "course_slug": self.course_family.slug,
                "cohort_identifier": self.cohort.identifier,
                "module_slug": self.module.slug,
            },
        )

    def test_the_lesson_page_offers_both_halves_of_the_control(self):
        html = self.client.get(self.unit_url()).content.decode()

        self.assertIn('id="module-rail-collapse"', html)
        self.assertIn('id="module-rail-restore"', html)
        self.assertIn('aria-label="Hide the module lessons"', html)
        self.assertIn('aria-label="Show the module lessons"', html)
        # Exactly one rail to control, named by the same heading it always had.
        self.assertEqual(html.count('id="module-rail"'), 1)
        self.assertEqual(html.count('aria-controls="module-rail"'), 2)
        self.assertIn('aria-labelledby="module-navigation-heading"', html)

    def test_each_half_declares_the_state_a_reader_can_see(self):
        html = self.client.get(self.unit_url()).content.decode()

        collapse = html[html.index('id="module-rail-collapse"') - 200 :][:600]
        restore = html[html.index('id="module-rail-restore"') - 200 :][:600]
        # Only one of the two is ever displayed, so each one's state is a
        # constant: the collapse half is drawn when the rail is expanded, the
        # restore half when it is hidden.
        self.assertIn('aria-expanded="true"', collapse)
        self.assertIn('aria-expanded="false"', restore)
        # Both halves are public controls, so both carry the one motion
        # primitive every other button on the site uses.  The site-wide contract
        # lives in `core.tests.test_interactive_surfaces`; this pins it here
        # because the pair is drawn by CSS rather than by `core/_button.html`.
        self.assertIn("interactive-lift", collapse)
        self.assertIn("interactive-lift", restore)

    def test_the_hidden_state_is_resolved_before_the_first_paint(self):
        html = self.client.get(self.unit_url()).content.decode()

        head = html[: html.index("<body")]
        self.assertIn("moduleRailCollapsed", head)
        self.assertIn("data-module-rail", head)
        # The collapsed rail leaves the document rather than being pushed
        # offscreen, so it leaves the accessibility tree and the tab order too.
        self.assertIn(
            ':root[data-module-rail="collapsed"] .module-rail {\n        display: none;',
            html,
        )

    def test_the_preference_never_reaches_the_response(self):
        anonymous = self.client.get(self.unit_url())
        get_user_model().objects.create_user(username="rail-reader", password="rail-pass")
        self.client.force_login(get_user_model().objects.get(username="rail-reader"))
        signed_in = self.client.get(self.unit_url())

        # No cookie, no Vary key and no session state is spent on a chrome
        # preference: the anonymous lesson stays edge-cacheable.
        self.assertNotIn("moduleRailCollapsed", anonymous.cookies)
        self.assertNotIn("moduleRailCollapsed", signed_in.cookies)
        self.assertNotIn("moduleRailCollapsed", self.client.session.keys())
        self.assertIn(
            "data-module-rail",
            anonymous.content.decode()[: anonymous.content.decode().index("<body")],
        )

    def test_the_module_index_keeps_its_rail(self):
        html = self.client.get(self.module_url()).content.decode()

        self.assertIn('id="module-rail"', html)
        self.assertNotIn('id="module-rail-collapse"', html)
        self.assertNotIn('id="module-rail-restore"', html)
        self.assertNotIn("moduleRailCollapsed", html)
