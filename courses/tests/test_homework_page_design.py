"""Design contracts for the redesigned homework page.

The page states facts in the cream header and exactly one state notice in the
lavender band, carries no coloured accent border, and always offers a way back
to the module it closes.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase
from django.utils import timezone

from courses.models.curriculum import Module
from courses.tests.homework_view_base import HomeworkDetailViewTestBase

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
HOMEWORK_TEMPLATE = REPOSITORY_ROOT / "courses/templates/homework/homework.html"
SUBMISSION_SHELL = REPOSITORY_ROOT / "courses/templates/courses/_submission_page.html"


def page_styles(template_path: Path) -> str:
    source = template_path.read_text(encoding="utf-8")
    return source.split("{% block extra_styles %}", 1)[1].split("{% endblock %}", 1)[0]


class HomeworkAccentBorderTests(SimpleTestCase):
    """No coloured single-side accent border is drawn by this page."""

    def test_page_styles_neutralise_the_shared_callout_tone_rail(self) -> None:
        styles = page_styles(HOMEWORK_TEMPLATE)
        callout_rule = re.search(
            r"\.submission-band \.callout \{(.*?)\}", styles, re.DOTALL
        )

        self.assertIsNotNone(callout_rule)
        assert callout_rule is not None
        self.assertIn("border: 2px solid var(--line-soft)", callout_rule.group(1))
        self.assertIn("background: var(--card)", callout_rule.group(1))

    def test_page_styles_declare_no_coloured_single_side_border(self) -> None:
        for template_path in (HOMEWORK_TEMPLATE, SUBMISSION_SHELL):
            with self.subTest(template=template_path.name):
                source = template_path.read_text(encoding="utf-8")
                for property_name in (
                    "border-inline-start:",
                    "border-inline-end:",
                    "border-left:",
                    "border-right:",
                ):
                    matches = re.findall(
                        rf"{re.escape(property_name)}[^;]*;", source
                    )
                    for declaration in matches:
                        self.assertIn(
                            "var(--line-soft)",
                            declaration,
                            msg=f"{property_name} must stay a neutral rule",
                        )

    def test_the_form_box_is_removed_without_losing_the_shared_measure(self) -> None:
        """The removal moved into the primitive, so this page scopes nothing."""

        styles = page_styles(HOMEWORK_TEMPLATE)
        self.assertNotIn(".submission-band .cmp-form", styles)

        design_system = (
            REPOSITORY_ROOT / "templates/core/_design_system.html"
        ).read_text(encoding="utf-8")
        form_rule = re.search(r"\n      \.cmp-form \{(.*?)\}", design_system, re.DOTALL)

        self.assertIsNotNone(form_rule)
        assert form_rule is not None
        self.assertNotIn("background", form_rule.group(1))
        self.assertNotIn("border", form_rule.group(1))
        self.assertNotIn("padding", form_rule.group(1))
        self.assertIn("max-width: var(--form-measure)", form_rule.group(1))
        self.assertIn("gap: 1.25rem", form_rule.group(1))


class HomeworkPageStructureTests(HomeworkDetailViewTestBase):
    def band_html(self, response) -> str:
        body = response.content.decode()
        return body.split('content-page-content submission-band', 1)[1]

    def header_html(self, response) -> str:
        body = response.content.decode()
        header = body.split("content-page-header submission-hero", 1)[1]
        return header.split('content-page-content submission-band', 1)[0]

    def test_state_notices_render_in_the_content_band_not_the_header(self) -> None:
        response = self.get_homework_response()

        self.assertIn('<div class="homework-state-notices">', self.band_html(response))
        self.assertNotIn("callout", self.header_html(response))
        self.assertNotIn("submission-notices", self.band_html(response))

    def test_header_states_facts_in_what_when_act_order(self) -> None:
        self.homework.instructions_url = "https://github.com/DataTalksClub/llm-zoomcamp"
        self.homework.save(update_fields=["instructions_url"])

        header = self.header_html(self.get_homework_response())
        heading = header.index("submission-heading")
        deadline = header.index("homework-deadline")
        hint = header.index("field-hint")
        instructions = header.index("Instructions on GitHub")

        self.assertLess(heading, deadline)
        self.assertLess(deadline, hint)
        self.assertLess(hint, instructions)

    def test_question_metadata_uses_the_mono_label_voice(self) -> None:
        response = self.get_homework_response()

        self.assertContains(response, 'class="question-note mono-label"')
        self.assertNotContains(response, "(not graded)")


class HomeworkModuleTrailTests(HomeworkDetailViewTestBase):
    def add_module(self):
        self.course.curriculum_format = "modules"
        self.course.save(update_fields=["curriculum_format"])
        return Module.objects.create(
            cohort=self.course,
            position=1,
            slug="module-one",
            title="Module 1: Agentic RAG",
            terminal_homework=self.homework,
        )

    def test_module_crumb_and_back_link_appear_for_a_module_homework(self) -> None:
        module = self.add_module()

        response = self.get_homework_response()
        body = response.content.decode()
        breadcrumb = re.search(
            r'<nav class="breadcrumbs" aria-label="Breadcrumb">(.*?)</nav>',
            body,
            re.DOTALL,
        )

        self.assertEqual(response.context["homework_module"], module)
        self.assertIsNotNone(breadcrumb)
        assert breadcrumb is not None
        self.assertIn(module.title, breadcrumb.group(1))
        # The trail still stops at the parent: the homework title is the h1.
        self.assertNotIn(self.homework.title, breadcrumb.group(1))
        self.assertContains(response, f"← Back to {module.title}")

    def test_a_homework_without_a_module_keeps_the_shorter_trail(self) -> None:
        response = self.get_homework_response()
        body = response.content.decode()
        breadcrumb = re.search(
            r'<nav class="breadcrumbs" aria-label="Breadcrumb">(.*?)</nav>',
            body,
            re.DOTALL,
        )

        self.assertIsNone(response.context["homework_module"])
        self.assertIsNotNone(breadcrumb)
        assert breadcrumb is not None
        self.assertNotIn("modules/", breadcrumb.group(1))
        self.assertNotContains(response, "← Back to")


class HomeworkStateMatrixTests(HomeworkDetailViewTestBase):
    """One notice per state, with the copy the design specification names."""

    def set_state(self, state, *, deadline_passed=False):
        self.homework.state = state
        offset = -2 if deadline_passed else 7
        self.homework.due_date = timezone.now() + timezone.timedelta(days=offset)
        self.homework.save(update_fields=["state", "due_date"])

    def callouts(self, response):
        return re.findall(
            r'<div class="callout callout-(\w+)"[^>]*>(.*?)</div>',
            response.content.decode(),
            re.DOTALL,
        )

    def assert_single_notice(self, response, tone, *fragments):
        callouts = self.callouts(response)

        self.assertEqual(len(callouts), 1, msg=f"expected one notice, got {callouts}")
        self.assertEqual(callouts[0][0], tone)
        for fragment in fragments:
            self.assertIn(fragment, callouts[0][1])

    def test_signed_out_open_before_deadline(self) -> None:
        self.set_state("OP")

        self.assert_single_notice(
            self.get_homework_response(),
            "info",
            "Log in to submit this homework.",
            "You can preview the questions below; submissions are saved only "
            "for logged-in students.",
            "Log in to submit",
        )

    def test_signed_out_open_after_deadline(self) -> None:
        self.set_state("OP", deadline_passed=True)

        self.assert_single_notice(
            self.get_homework_response(),
            "info",
            "Log in to submit — late submissions are still accepted.",
            "answers are accepted until the homework is closed for scoring",
        )

    def test_signed_out_closed(self) -> None:
        self.set_state("CL", deadline_passed=True)

        self.assert_single_notice(
            self.get_homework_response(),
            "attention",
            "This homework is closed.",
            "to see the status of your submission.",
        )

    def test_signed_out_scored(self) -> None:
        self.set_state("SC", deadline_passed=True)

        self.assert_single_notice(
            self.get_homework_response(),
            "info",
            "Correct answers are shown below.",
            "Log in to see your own submission, score, and feedback.",
            "Log in to view my results",
        )

    def test_signed_in_open_before_deadline_is_quiet(self) -> None:
        self.set_state("OP")

        self.assertEqual(self.callouts(self.get_homework_response(login=True)), [])

    def test_signed_in_open_after_deadline(self) -> None:
        self.set_state("OP", deadline_passed=True)

        self.assert_single_notice(
            self.get_homework_response(login=True),
            "info",
            "Still accepting late submissions.",
            "Your latest saved version will be scored when the homework closes.",
        )

    def test_signed_in_closed_with_submission(self) -> None:
        self.create_submission_with_answers()
        self.set_state("CL", deadline_passed=True)

        response = self.get_homework_response(login=True)

        self.assert_single_notice(
            response,
            "attention",
            "This homework is closed.",
            "Your saved submission is shown below and cannot be changed.",
        )
        # A control that `handle_homework_post` would reject is not offered.
        self.assertNotContains(response, "Update submission")
        self.assertNotContains(response, "until the deadline")

    def test_signed_in_closed_without_submission_still_says_so(self) -> None:
        self.set_state("CL", deadline_passed=True)

        response = self.get_homework_response(login=True)

        self.assert_single_notice(
            response,
            "attention",
            "This homework is not open for submissions.",
        )

    def test_signed_in_scored_with_submission(self) -> None:
        self.create_submission_with_answers()
        self.set_state("SC", deadline_passed=True)

        self.assert_single_notice(
            self.get_homework_response(login=True),
            "info",
            "Scored homework",
            "Your submission has been graded.",
            "callout-score",
        )

    def test_signed_in_scored_without_submission(self) -> None:
        self.set_state("SC", deadline_passed=True)

        self.assert_single_notice(
            self.get_homework_response(login=True),
            "attention",
            "This homework was scored without a submission from you.",
        )
