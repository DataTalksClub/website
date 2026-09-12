"""The learning-in-public link rows are labelled, indexed, and cap-aware.

The editable rows once rendered as bare ``<input type="url">`` elements: the
group heading was a ``<label>`` whose ``for`` pointed at the field *name*
(``learning_in_public_links[]``) and so matched nothing, no row carried an id
or a label, the hint was unreferenceable, and the "Add link" button rendered
even when the cap was already reached (audit UX-04).  These tests pin the
rendered contract: an indexed id plus sr-only label per row, the shared hint
referenced through aria-describedby, a row template the script clones, and a
visible cap note replacing the button at the cap.  The POST error re-render
must bring the rejected links back as the same labelled, indexed rows, so
submission errors never return the member to anonymous inputs.
"""

import re

from courses.models import Enrollment, Submission
from courses.tests.homework_submission_validation_base import (
    HomeworkSubmissionValidationBase,
    credentials,
)


def _input_tag(html: str, index: int) -> str:
    match = re.search(
        rf'<input\b[^>]*\bid="learning-in-public-link-{index}"[^>]*>',
        html,
        re.DOTALL,
    )
    assert match, f"no learning-in-public input row for index {index}"
    return match.group(0)


def _label_for(html: str, index: int) -> str:
    match = re.search(
        rf'<label\b[^>]*\bfor="learning-in-public-link-{index}"[^>]*>'
        rf"(.*?)</label>",
        html,
        re.DOTALL,
    )
    assert match, f"no label for learning-in-public link {index}"
    return match.group(0)


def _cap_note_tag(html: str) -> str:
    match = re.search(r'<p\b[^>]*\bid="learning-in-public-cap-note"[^>]*>', html, re.DOTALL)
    assert match, "no learning-in-public cap note on the page"
    return match.group(0)


class HomeworkSubmissionLearningPublicMarkupTests(HomeworkSubmissionValidationBase):
    def setUp(self):
        super().setUp()
        self.homework.learning_in_public_cap = 2
        self.homework.save()

    def get_homework_page(self):
        self.client.login(**credentials)
        return self.client.get(self.homework_url())

    def create_submission_with_links(self, links):
        enrollment = Enrollment.objects.create(student=self.user, course=self.course)
        return Submission.objects.create(
            homework=self.homework,
            student=self.user,
            enrollment=enrollment,
            learning_in_public_links=links,
        )

    def test_editable_rows_are_labelled_indexed_and_hinted(self):
        response = self.get_homework_page()
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()

        # The group heading is a plain heading: no label may point its for=
        # at the field name, which matches no id on the page.
        self.assertNotContains(response, 'for="learning_in_public_links[]"')
        self.assertContains(response, 'id="learning-in-public-group-label"')

        # The hint is referenceable, the row template is present for the
        # "Add link" script, and the empty row is a labelled control.
        self.assertContains(response, 'id="learning-in-public-hint"')
        self.assertContains(response, 'id="learning-in-public-row-template"')
        first_row = _input_tag(html, 1)
        self.assertIn('type="url"', first_row)
        self.assertIn('name="learning_in_public_links[]"', first_row)
        self.assertIn('aria-describedby="learning-in-public-hint"', first_row)
        first_label = _label_for(html, 1)
        self.assertIn('class="sr-only"', first_label)
        self.assertIn("Learning in public link 1", first_label)

        # Below the cap: the Add button renders and the cap note is hidden.
        self.assertContains(response, 'id="add-learning-public-link"')
        self.assertIn("hidden", _cap_note_tag(html))

    def test_prefilled_rows_index_after_the_saved_links(self):
        self.create_submission_with_links(["https://example.com/post-1"])
        response = self.get_homework_page()
        html = response.content.decode()

        first_row = _input_tag(html, 1)
        self.assertIn('value="https://example.com/post-1"', first_row)
        self.assertIn("Learning in public link 1", _label_for(html, 1))

        # The empty next row continues the numbering; at cap 2 nothing
        # follows it.
        second_row = _input_tag(html, 2)
        self.assertNotIn("value=", second_row)
        self.assertIn("Learning in public link 2", _label_for(html, 2))
        with self.assertRaises(AssertionError):
            _input_tag(html, 3)

        # One saved link is still below the cap, so the button remains.
        self.assertContains(response, 'id="add-learning-public-link"')

    def test_at_cap_the_add_button_yields_to_a_visible_cap_note(self):
        self.create_submission_with_links(
            [
                "https://example.com/post-1",
                "https://example.com/post-2",
            ]
        )
        response = self.get_homework_page()
        html = response.content.decode()

        self.assertNotContains(response, 'id="add-learning-public-link"')
        self.assertNotIn("hidden", _cap_note_tag(html))
        self.assertContains(response, "You have added the maximum of 2 links")

    def test_error_rerender_keeps_row_identity_and_flags_the_rows(self):
        response = self.post_homework(
            self.updated_answer_post_data(**{"learning_in_public_links[]": ["javascript:alert(1)"]})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Learning in public links must be valid HTTP or HTTPS URLs.",
        )
        html = response.content.decode()

        # The rejected submission comes back as the same labelled rows, not
        # as anonymous inputs: identity survives, with the submitted value
        # still bound.  (The server names the failure through the error
        # summary; the per-row invalid wiring is the client-side contract
        # covered by the Playwright suite.)
        first_row = _input_tag(html, 1)
        self.assertIn('value="javascript:alert(1)"', first_row)
        self.assertIn("Learning in public link 1", _label_for(html, 1))
