import re
from pathlib import Path

from django.urls import reverse

from courses.tests.homework_view_base import (
    HomeworkDetailViewTestBase,
)


class HomeworkDetailViewTests(HomeworkDetailViewTestBase):
    def test_homework_page_uses_homework_title_as_heading_and_keeps_course_context(self):
        response = self.get_homework_response()
        body = response.content.decode()
        breadcrumb = re.search(
            r'<nav class="shell shell-reading breadcrumbs" aria-label="Breadcrumb">'
            r"(.*?)</nav>",
            body,
            re.DOTALL,
        )

        self.assertIsNotNone(breadcrumb)
        assert breadcrumb is not None
        self.assertContains(
            response,
            f'<h1 id="submission-heading">{self.homework.title}</h1>',
        )
        self.assertIn(self.course.title, breadcrumb.group(1))
        self.assertNotIn(self.homework.title, breadcrumb.group(1))
        self.assertNotIn("Homework submission", body)
        self.assertIn('aria-labelledby="submission-heading"', body)

    def test_homework_questions_use_borderless_fieldsets_and_shared_field_primitives(self):
        response = self.get_homework_response()
        body = response.content.decode()
        template_path = (
            Path(__file__).resolve().parents[1] / "templates/homework/homework.html"
        )
        template = template_path.read_text(encoding="utf-8")
        extra_styles = template.split("{% block extra_styles %}", 1)[1].split(
            "{% endblock %}", 1
        )[0]
        question_rule = re.search(r"\.question\s*\{(.*?)\}", extra_styles, re.DOTALL)

        self.assertIsNotNone(question_rule)
        assert question_rule is not None
        self.assertIn("background: transparent", question_rule.group(1))
        self.assertIn("border: 0", question_rule.group(1))
        self.assertIn("gap: 0.75rem", question_rule.group(1))
        self.assertIn("padding: 0", question_rule.group(1))
        self.assertNotIn("border: 2px", question_rule.group(1))
        self.assertNotIn("border-radius", question_rule.group(1))
        self.assertIn("border: 2px solid var(--line)", extra_styles)
        self.assertNotIn("Answer the questions below to complete your homework.", body)
        self.assertNotIn('<div class="submission-support">', body)
        self.assertContains(
            response,
            '<form method="post" class="needs-validation cmp-form homework-form submission-form"',
        )
        self.assertContains(response, '<fieldset class="question">', count=6)
        self.assertContains(response, 'aria-labelledby="question-label-')
        self.assertContains(response, 'class="field-input form-control')

    def test_homework_detail_unauthenticated(self):
        response = self.get_homework_response()

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "homework/homework.html")

        context = self.assert_homework_context(response, is_authenticated=False)
        self.assert_empty_question_answers(context["question_answers"])

        self.assertNotContains(response, "Shown in your timezone.")
        self.assertNotContains(response, "account timezone")

    def test_homework_detail_unauthenticated_hides_submission_fields(self):
        self.enable_all_optional_submission_fields()

        response = self.get_homework_response()

        self.assertEqual(response.status_code, 200)
        self.assert_unauthenticated_submission_preview(response)
        self.assert_submission_fields_hidden(response)

    def test_homework_detail_displays_optional_instructions_url(self):
        self.homework.instructions_url = (
            "https://github.com/DataTalksClub/course-management-platform/blob/main/README.md"
        )
        self.homework.save()

        url = reverse(
            "homework",
            kwargs={
                "course_slug": self.course.slug,
                "homework_slug": self.homework.slug,
            },
        )
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Instructions")
        self.assertContains(response, self.homework.instructions_url)
        # The design 5a page loads no icon font, so the GitHub instructions
        # link is named rather than marked with a glyph (issue #179).
        self.assertContains(response, "Instructions on GitHub")

    def test_homework_detail_hides_missing_instructions_url(self):
        self.homework.instructions_url = ""
        self.homework.save()

        url = reverse(
            "homework",
            kwargs={
                "course_slug": self.course.slug,
                "homework_slug": self.homework.slug,
            },
        )
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Instructions")

    def test_homework_detail_authenticated_no_submission(self):
        response = self.get_homework_response(login=True)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "homework/homework.html")

        context = self.assert_homework_context(
            response, is_authenticated=True
        )

        self.assert_empty_question_answers(context["question_answers"])
        self.assertEqual(context["homework"].due_date, self.homework.due_date)
        self.assertContains(response, "Status: Not saved yet")
        self.assertContains(response, "Save submission")
        self.assertContains(
            response,
            (
                "You can save partial answers and update them until the "
                "deadline. Your latest saved version will be scored."
            ),
        )

    def test_homework_detail_authenticated_with_submission(self):
        self.create_submission_with_answers()

        response = self.get_homework_response(login=True)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "homework/homework.html")

        context = self.assert_homework_context(response, is_authenticated=True)
        self.assertEqual(context["submission"], self.submission)

        self.assert_saved_question_answers(context["question_answers"])
        self.assertContains(response, "Status: Last saved at")
        self.assertContains(response, "Update submission")
        self.assertContains(
            response,
            (
                "You can save partial answers and update them until the "
                "deadline. Your latest saved version will be scored."
            ),
        )
