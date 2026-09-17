from __future__ import annotations

from datetime import date
from pathlib import Path

from django.conf import settings
from django.template import Context, Template
from django.test import SimpleTestCase

from core import mail_templates
from core.accessibility_registry import (
    ACCESSIBILITY_AUTHORED_TEMPLATES,
    AUTHORED_TEMPLATE_ROOTS,
    AXE_EXCEPTIONS,
    BEHAVIOR_SCENARIOS,
    CRITICAL_STATES,
    NO_JAVASCRIPT_PUBLIC_STATE_IDS,
    registry_fingerprint,
    template_readability_issues,
    template_surface,
)
from core.mail_render_fixtures import render_package_template


class FocusStyleContractTests(SimpleTestCase):
    def test_pointer_focus_is_unstyled_while_keyboard_focus_uses_design_token(self) -> None:
        stylesheet = (Path(settings.BASE_DIR) / "core/static/core/accessibility.css").read_text(
            encoding="utf-8"
        )

        interactive_selector = (
            "html body :is(a, button, input, select, textarea, summary, [tabindex]):focus-visible"
        )
        self.assertEqual(stylesheet.count(f"{interactive_selector} {{"), 2)
        self.assertIn(
            "outline: 3px solid var(--focus-ring, #315f8f) !important;",
            stylesheet,
        )
        self.assertNotIn(
            "html body :is(a, button, input, select, textarea, summary, [tabindex]):focus {",
            stylesheet,
        )
        self.assertNotIn("--a11y-focus", stylesheet)

    def test_skip_link_and_programmatic_main_focus_exceptions_remain_scoped(self) -> None:
        stylesheet = (Path(settings.BASE_DIR) / "core/static/core/accessibility.css").read_text(
            encoding="utf-8"
        )

        self.assertIn(".skip-link:focus {", stylesheet)
        self.assertIn("#main-content:focus {", stylesheet)
        self.assertIn("outline: 0 !important;", stylesheet)


class AccessibilityRegistryTests(SimpleTestCase):
    def test_registry_identifiers_and_rendered_surfaces_are_fail_closed(self) -> None:
        identifiers = [state.identifier for state in CRITICAL_STATES]
        self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertGreaterEqual(len(CRITICAL_STATES), 80)
        for state in CRITICAL_STATES:
            with self.subTest(state=state.identifier):
                self.assertIn(state.behavior_test, BEHAVIOR_SCENARIOS)
                if state.axe_surface is None:
                    self.assertTrue(state.route_contract)
        self.assertRegex(registry_fingerprint(), r"^[0-9a-f]{64}$")

    def test_no_javascript_public_policy_is_exactly_rendered_public_states(self) -> None:
        self.assertEqual(len(NO_JAVASCRIPT_PUBLIC_STATE_IDS), 26)
        self.assertEqual(
            len(NO_JAVASCRIPT_PUBLIC_STATE_IDS),
            len(set(NO_JAVASCRIPT_PUBLIC_STATE_IDS)),
        )
        states = {state.identifier: state for state in CRITICAL_STATES}
        for identifier in NO_JAVASCRIPT_PUBLIC_STATE_IDS:
            with self.subTest(state=identifier):
                state = states[identifier]
                self.assertEqual(state.group, "public")
                self.assertEqual(state.behavior_test, "public-current-states")
                self.assertIsNotNone(state.axe_surface)
                self.assertFalse(state.route_contract)

    def test_every_authored_product_template_has_one_surface_owner(self) -> None:
        root = Path(settings.BASE_DIR)
        candidates: set[Path] = set()
        for location in (
            "course_platform_templates",
            "templates",
            "accounts/templates",
            "courses/templates",
            "studio_courses/templates",
        ):
            candidates.update((root / location).rglob("*.html"))
        candidates.difference_update((root / "templates" / "management_api").rglob("*.html"))
        missing = [
            path.relative_to(root).as_posix()
            for path in sorted(candidates)
            if template_surface(path.relative_to(root).as_posix()) is None
        ]
        self.assertEqual(missing, [])
        self.assertIn("course_platform_templates", AUTHORED_TEMPLATE_ROOTS)

    def test_axe_exceptions_require_exact_selector_owner_reason_and_future_expiry(self) -> None:
        known_states = {state.identifier for state in CRITICAL_STATES}
        for exception in AXE_EXCEPTIONS:
            with self.subTest(rule=exception.rule, state=exception.state):
                self.assertIn(exception.state, known_states)
                self.assertTrue(exception.rule and exception.selector.startswith(("#", ".", "[")))
                self.assertNotIn(exception.selector, {"*", "body", "html"})
                self.assertTrue(exception.reason and exception.owner)
                self.assertGreater(exception.expires, date.today())

    def test_every_issue_authored_template_is_readable(self) -> None:
        root = Path(settings.BASE_DIR)
        failures: list[str] = []
        for relative in ACCESSIBILITY_AUTHORED_TEMPLATES:
            path = root / relative
            self.assertTrue(path.is_file(), relative)
            failures.extend(
                f"{relative}:{issue}"
                for issue in template_readability_issues(path.read_text(encoding="utf-8"))
            )
        self.assertEqual(failures, [])

    def test_readability_scan_rejects_compacted_template(self) -> None:
        unreadable = (
            "{% if value %}<section><h2>Value</h2></section>{% endif %}"
            '<ul><li><a href="/">Item</a></li></ul>'
        )
        issues = template_readability_issues(unreadable)
        self.assertTrue(issues)
        self.assertLessEqual(len("; ".join(issues)), 500)


class AccessibleFormPrimitiveTests(SimpleTestCase):
    def test_widget_errors_are_linked_and_summary_is_navigable(self) -> None:
        from django import forms

        class FixtureForm(forms.Form):
            name = forms.CharField(help_text="Use a public display name.")

        form = FixtureForm(data={"name": ""})
        self.assertFalse(form.is_valid())
        rendered = Template(
            "{% load accessibility %}"
            "{% accessibility_error_summary form %}"
            "{% accessible_widget form.name %}"
        ).render(Context({"form": form}))
        self.assertIn("data-focus-error-summary", rendered)
        self.assertIn('href="#id_name"', rendered)
        self.assertIn('aria-invalid="true"', rendered)
        self.assertIn('aria-errormessage="id_name-error"', rendered)
        self.assertIn('aria-describedby="id_name-help id_name-error"', rendered)


class AccessibleEmailFixtureTests(SimpleTestCase):
    def test_every_committed_package_template_renders_accessible_html(self) -> None:
        templates = {t.key: t for t in mail_templates.load_templates()}
        self.assertGreaterEqual(len(templates), 8)
        for template_key, template in templates.items():
            with self.subTest(template=template_key):
                rendered = render_package_template(template_key)
                self.assertIn("<title>", rendered.html)
                self.assertIn('<html lang="en">', rendered.html)
                self.assertNotIn("javascript:", rendered.html)
                self.assertNotIn("{{", rendered.html)
                self.assertNotIn("javascript:", rendered.html)
                self.assertNotIn("{{", rendered.html)

    def test_fixture_is_bound_to_current_registration_and_score_flows(self) -> None:
        registration = render_package_template("course-registration-confirmation")
        score = render_package_template("homework-score-notification")

        self.assertEqual(
            registration.subject,
            "You are registered for Q2Z",
        )
        self.assertIn('href="Q3Z"', registration.html)
        self.assertEqual(score.subject, "Scores available: Q5Z")
        self.assertIn("Review your homework score", score.html)
