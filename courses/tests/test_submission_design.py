"""Focused contracts for the shared learner submission design primitives."""

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
        self.assertIn('class="cta cta-primary cta-compact"', rendered)
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
        self.assertIn("border-inline-start: 0", quiet_rule)
        self.assertIn("background: var(--card)", quiet_rule)
        self.assertIn("var(--focus)", design_system)


class SubmissionTemplateStructureTests(SimpleTestCase):
    def test_course_catalogue_actions_use_design_tokens_without_the_old_ink_cta(self) -> None:
        source = read_template("courses/templates/courses/course_list.html")

        self.assertIn('class="cta cta-secondary cta-compact courses-wrapped-action"', source)
        self.assertIn('class="cta cta-primary cta-compact"', source)
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
        self.assertIn('class="submission-support"', source)
        self.assertIn("border: 0", source)
        self.assertIn(
            (
                'classes="callout-quiet" message="No public answers are '
                'available for this homework yet."'
            ),
            form_source,
        )
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
        self.assertIn(
            '<nav class="breadcrumbs"',
            source,
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
