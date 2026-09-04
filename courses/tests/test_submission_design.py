"""Focused contracts for the shared learner submission design primitives."""

import re
from pathlib import Path

from django.template.loader import get_template
from django.test import SimpleTestCase

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def read_template(relative_path: str) -> str:
    return (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")


class SharedSubmissionPrimitiveTests(SimpleTestCase):
    def test_button_include_renders_the_three_token_variants(self) -> None:
        template = get_template("core/_button.html")

        for variant in ("primary", "secondary", "subtle"):
            with self.subTest(variant=variant):
                rendered = template.render(
                    {
                        "href": "/courses/example/",
                        "label": "Action",
                        "variant": variant,
                    }
                )
                self.assertIn(f'class="cta cta-{variant}', rendered)

        rendered_button = template.render(
            {
                "tag": "button",
                "type": "submit",
                "label": "Save",
                "variant": "primary",
            }
        )
        self.assertIn('<button\n    class="cta cta-primary', rendered_button)
        self.assertIn('type="submit"', rendered_button)

    def test_callout_include_preserves_tones_and_accessible_roles(self) -> None:
        template = get_template("core/_callout.html")

        info = template.render(
            {"tone": "info", "role": "status", "message": "Saved"}
        )
        attention = template.render(
            {"tone": "attention", "role": "alert", "message": "Check this"}
        )
        quiet = template.render(
            {
                "tone": "info",
                "classes": "callout-quiet",
                "message": "No public answers",
            }
        )

        self.assertIn("callout callout-info", info)
        self.assertIn('role="status"', info)
        self.assertIn("callout callout-attention", attention)
        self.assertIn('role="alert"', attention)
        self.assertIn("callout callout-info callout-quiet", quiet)

    def test_callout_action_does_not_inherit_callout_classes(self) -> None:
        template = get_template("core/_callout.html")

        rendered = template.render(
            {
                "tone": "info",
                "classes": "callout-quiet",
                "button_url": "/accounts/login/",
                "button_label": "Log in",
                "button_variant": "primary",
            }
        )

        self.assertIn('class="callout callout-info callout-quiet"', rendered)
        self.assertIn('class="cta cta-primary cta-compact interactive-lift"', rendered)
        self.assertNotIn("cta-primary cta-compact callout-quiet", rendered)

    def test_subtle_button_and_quiet_callout_are_token_driven(self) -> None:
        design_system = read_template("templates/core/_design_system.html")
        subtle_start = design_system.index(".cta-subtle,")
        subtle_end = design_system.index(".cta-subtle:hover", subtle_start)
        subtle_rule = design_system[subtle_start:subtle_end]
        quiet_start = design_system.index(".callout-quiet {")
        quiet_end = design_system.index(".callout strong", quiet_start)
        quiet_rule = design_system[quiet_start:quiet_end]

        self.assertIn("background: transparent", subtle_rule)
        self.assertIn("box-shadow: none", subtle_rule)
        self.assertIn("var(--line-soft)", subtle_rule)
        self.assertIn("#learning-in-public-links-container .cta-secondary", design_system)
        self.assertIn(".project-toolbar .cta-secondary", design_system)
        # The quiet callout is the dashed plate on whatever surface it sits on,
        # not a second tone rail: the system draws no accent left border at all
        # (see `core/tests/test_design_accent_borders.py`).
        self.assertIn("background: transparent", quiet_rule)
        self.assertIn("border-style: dashed", quiet_rule)
        self.assertNotIn("border-inline-start", quiet_rule)
        self.assertIn("var(--focus)", design_system)

    def test_cmp_form_is_a_measure_and_not_a_box(self) -> None:
        """The form sits on the lavender band; it does not repaint it."""

        design_system = read_template("templates/core/_design_system.html")
        start = design_system.index(".cmp-form {")
        rule = design_system[start : design_system.index("}", start)]

        self.assertIn("display: grid", rule)
        self.assertIn("gap: 1.25rem", rule)
        self.assertIn("max-width: var(--form-measure)", rule)
        self.assertIn("width: 100%", rule)
        self.assertNotIn("background", rule)
        self.assertNotIn("border", rule)
        self.assertNotIn("padding", rule)

    def test_disabled_controls_recede_while_readonly_keeps_its_border(self) -> None:
        """WCAG 1.4.11 exempts inactive components, not focusable readonly ones."""

        design_system = read_template("templates/core/_design_system.html")
        start = design_system.index(
            ".field-input[disabled],\n      .form-control[disabled] {"
        )
        rule = design_system[start : design_system.index("}", start)]

        self.assertIn("border-color: var(--line-soft)", rule)
        self.assertNotIn("[readonly]", rule)
        # The shared surface/text treatment still covers both states.
        shared_start = design_system.index(".field-input[readonly],")
        shared_rule = design_system[shared_start : design_system.index("}", shared_start)]
        self.assertIn("background: var(--sand)", shared_rule)
        self.assertIn("color: var(--muted)", shared_rule)
        self.assertNotIn("border-color", shared_rule)

    def test_choice_controls_use_the_shared_size_without_page_overrides(self) -> None:
        design_system = read_template("templates/core/_design_system.html")
        start = design_system.index(".form-check-input {")
        rule = design_system[start : design_system.index("}", start)]
        row_start = design_system.index(".form-check {")
        row_rule = design_system[row_start : design_system.index("}", row_start)]

        self.assertIn("height: 1.25rem", rule)
        self.assertIn("width: 1.25rem", rule)
        # The 44px pointer target is the row, so the larger control does not
        # change it.
        self.assertIn("min-height: 2.75rem", row_rule)
        # Page styles are emitted after the design system inside the same style
        # element, so an equal-specificity restatement silently wins.  Every
        # page that includes the design system and draws a checkbox is checked,
        # Studio included: the size is the system's, not the page's.
        choice_primitives = ("form-check", "form-check-input", "form-check-label")
        for path, primitives in (
            (
                "courses/templates/homework/homework.html",
                (*choice_primitives, "form-control", "cmp-form"),
            ),
            (
                "courses/templates/homework/_submission_form.html",
                (*choice_primitives, "form-control", "cmp-form"),
            ),
            (
                "courses/templates/projects/eval_submit.html",
                (*choice_primitives, "form-control", "cmp-form"),
            ),
            (
                "courses/templates/projects/project.html",
                (*choice_primitives, "form-control", "cmp-form"),
            ),
            # Studio still gives the copied widget names its own `.form-control`
            # treatment, which is out of this change's scope; the checkbox size
            # is not the page's to decide.
            (
                "studio_courses/templates/studio_courses/campaign_form.html",
                choice_primitives,
            ),
        ):
            with self.subTest(template=path):
                source = read_template(path)
                for primitive in primitives:
                    # A bare restatement of the primitive, not the pages' own
                    # qualified `.option-answer-* .form-check-input` marks.
                    self.assertIsNone(
                        re.search(rf"^\s*\.{primitive} \{{", source, re.MULTILINE),
                        f"{path} restates the .{primitive} primitive",
                    )


class SubmissionTemplateStructureTests(SimpleTestCase):
    def test_course_catalogue_actions_use_design_tokens_without_the_old_ink_cta(self) -> None:
        source = read_template("courses/templates/courses/course_list.html")

        self.assertIn(
            'class="cta cta-secondary cta-compact courses-wrapped-action interactive-lift"',
            source,
        )
        self.assertIn('class="cta cta-primary cta-compact interactive-lift"', source)
        self.assertIn(">Register</a>", source)
        self.assertNotIn("Continue course", source)
        self.assertNotIn("Open course", source)
        self.assertNotIn("cta-ink", source)

    def test_homework_uses_shared_shell_without_repeated_facts_or_one_off_boxes(self) -> None:
        source = read_template("courses/templates/homework/homework.html")
        form_source = read_template("courses/templates/homework/_submission_form.html")

        self.assertIn('{% extends "courses/_submission_page.html" %}', source)
        self.assertIn('{% include "homework/_submission_form.html" %}', source)
        self.assertNotIn("homework-notice", source)
        self.assertNotIn("homework-specs", source)
        self.assertIn('<h1 id="submission-heading">{{ homework.title }}</h1>', source)
        self.assertNotIn("Answer the questions below to complete your homework.", source)
        self.assertIn("border: 0", source)
        self.assertIn(
            'message="No public answers are available for this homework yet."',
            form_source,
        )
        # Two callout weights for the same kind of message are gone: the page
        # carries one notice treatment, so `callout-quiet` has nothing to
        # remove and is no longer used here.
        self.assertNotIn("callout-quiet", source)
        self.assertNotIn("callout-quiet", form_source)
        # The boilerplate line beside the questions heading duplicated the
        # hero's instructions button.
        self.assertNotIn("This form is only for submitting your answers", form_source)
        for field in (
            "answer_{{ question.id }}",
            'name="homework_url"',
            'name="time_spent_lectures"',
            'name="time_spent_homework"',
            'name="problems_comments"',
        ):
            with self.subTest(field=field):
                self.assertIn(field, form_source)

    def test_project_and_peer_review_extend_the_shared_submission_shell(self) -> None:
        project = read_template("courses/templates/projects/project.html")
        peer_review = read_template("courses/templates/projects/eval_submit.html")
        shared = read_template("courses/templates/courses/_submission_page.html")

        self.assertIn('{% extends "courses/_submission_page.html" %}', project)
        self.assertIn('{% extends "courses/_submission_page.html" %}', peer_review)
        self.assertIn('label="Project statistics" variant="subtle"', project)
        self.assertIn('label="Manage project in Studio" variant="subtle"', project)
        self.assertIn("include 'include/learning_in_public_links.html'", project)
        self.assertIn("include 'include/learning_in_public_links.html'", peer_review)
        self.assertIn('class="submission-hero-inner"', shared)
        self.assertIn('{% extends "core/content_page.html" %}', shared)
        self.assertIn('{% block content_band_class %}submission-band{% endblock %}', shared)
        for field in ("github_link", "commit_id", "time_spent", "certificate_name"):
            with self.subTest(field=field):
                self.assertIn(f'name="{field}"', project)
        for field in ("form_action", "submission_id", "note_to_peer", "time_spent_reviewing"):
            with self.subTest(field=field):
                self.assertIn(f'name="{field}"', peer_review)
        self.assertIn("{% csrf_token %}", project)
        self.assertIn("{% csrf_token %}", peer_review)

    def test_enrollment_uses_the_course_reading_width_and_lavender_content(self) -> None:
        source = read_template("courses/templates/courses/enrollment.html")
        course_source = read_template("courses/templates/courses/course.html")

        self.assertIn('{% extends "core/content_page.html" %}', source)
        # The trail is drawn by the shared {% breadcrumbs %} tag rather than by
        # nav markup this page writes for itself; the nav landmark lives once, in
        # templates/core/_breadcrumbs.html.
        self.assertIn("{% breadcrumbs ", source)
        self.assertNotIn('<nav class="breadcrumbs"', source)
        self.assertIn(
            '<nav class="{{ nav_class }}" aria-label="{{ aria_label }}">',
            read_template("templates/core/_breadcrumbs.html"),
        )
        self.assertIn('{% extends "core/content_page.html" %}', course_source)
        self.assertIn(
            'class="shell content-shell"',
            read_template("templates/core/content_page.html"),
        )
        self.assertNotIn('class="shell shell-reading', source)
        self.assertNotIn("enrollment-shell", source)
        self.assertNotIn("max-width: 46rem", source)
        self.assertIn('<form method="post">', source)
        self.assertIn("{% csrf_token %}", source)
