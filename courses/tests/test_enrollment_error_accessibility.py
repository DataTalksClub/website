"""UX-10 acceptance: enrollment errors draw the shared accessible form contract.

A failed save used to re-render this page with styled-invalid widgets and an
unannounced error box per field: no summary to land on, no relationship
between an error and its field. It now draws the same contract as the
registration and account pages (design system, "Forms"): the shared error
summary takes focus through ``core/accessibility.js``, every link in it
reaches its field, each failed widget carries ``aria-invalid`` and the id of
the error rendered beneath it, non-field errors are covered too, and the
member's typed values survive the round trip.
"""

from __future__ import annotations

from django.template.loader import render_to_string
from django.urls import reverse

from courses.tests.course_view_base import (
    CourseDetailViewTestBase,
    credentials,
)
from courses.views.forms import EnrollmentForm

LONG_NAME = "x" * 256
OVERLONG_ERROR = "Ensure this value has at most 255 characters"


class EnrollmentErrorContractTest(CourseDetailViewTestBase):
    def setUp(self):
        super().setUp()
        self.client.login(**credentials)
        self.url = reverse(
            "cohort_enrollment",
            kwargs={
                "course_slug": self.course.course.slug,
                "cohort_identifier": self.course.identifier,
            },
        )

    def _post(self, **overrides):
        payload = {
            "display_name": LONG_NAME,
            "certificate_name": "",
            "display_on_leaderboard": "on",
            "display_public_profile": "on",
        }
        payload.update(overrides)
        return self.client.post(self.url, payload)

    def test_an_overlong_display_name_draws_the_shared_summary(self):
        response = self._post()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "a11y-error-summary")
        self.assertContains(response, "data-focus-error-summary")
        self.assertContains(response, 'href="#id_display_name"')
        # The widget announces its state and points at the error drawn under
        # it, which opens with the screen-reader "Error:" prefix.
        self.assertContains(response, 'aria-invalid="true"')
        self.assertContains(response, 'aria-errormessage="id_display_name-error"')
        self.assertContains(
            response,
            'aria-describedby="id_display_name-help id_display_name-error"',
        )
        self.assertContains(response, 'id="id_display_name-error"')
        self.assertContains(response, '<span class="sr-only">Error:</span>')
        self.assertContains(response, OVERLONG_ERROR)
        # What the member typed is still on the page, still in the field.
        self.assertContains(response, f'value="{LONG_NAME[:32]}')

    def test_the_summary_reports_the_failed_field_and_nothing_else(self):
        response = self._post()

        body = response.content.decode()
        # Exactly one summary entry: the identity field that failed. The two
        # immediate-save privacy toggles are not fields this form validates,
        # and the summary must never name a switch the page already saved.
        self.assertIn('<a href="#id_display_name">', body)
        self.assertNotIn('<a href="#id_certificate_name">', body)
        self.assertNotIn('<a href="#id_display_on_leaderboard">', body)
        self.assertNotIn('<a href="#id_display_public_profile">', body)
        self.assertNotIn("is-invalid", body.rsplit('name="display_on_leaderboard"', 1)[1])

    def test_an_overlong_certificate_name_points_at_the_certificate_field(self):
        response = self._post(display_name="Ada Lovelace", certificate_name=LONG_NAME)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="#id_certificate_name"')
        self.assertContains(
            response,
            'aria-describedby="id_certificate_name-help id_certificate_name-error"',
        )
        self.assertContains(response, 'aria-errormessage="id_certificate_name-error"')
        self.assertContains(response, 'id="id_certificate_name-error"')
        self.assertNotIn('<a href="#id_display_name">', response.content.decode())

    def test_a_clean_page_draws_no_error_marks(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        # The class name alone also matches this page's inline CSS, so the
        # negative asserts look for the summary's rendered-only hooks.
        self.assertNotContains(response, "data-focus-error-summary")
        self.assertNotContains(response, "aria-invalid")
        # The help relationship is on the happy path too, not only under
        # failure: each hint is the field's described-by target.
        self.assertContains(response, 'aria-describedby="id_display_name-help"')
        self.assertContains(response, 'aria-describedby="id_certificate_name-help"')

    def test_a_failed_save_persists_nothing_and_a_fixed_one_recovers(self):
        failed = self._post()
        self.assertEqual(failed.status_code, 200)
        self.enrollment.refresh_from_db()
        self.assertNotEqual(self.enrollment.display_name, LONG_NAME)

        cohort_url = reverse(
            "cohort",
            kwargs={
                "course_slug": self.course.course.slug,
                "cohort_identifier": self.course.identifier,
            },
        )
        saved = self._post(display_name="Ada Lovelace")
        self.assertRedirects(saved, cohort_url, fetch_redirect_response=False)

        again = self.client.get(self.url)
        self.assertNotContains(again, "data-focus-error-summary")
        self.assertContains(again, 'value="Ada Lovelace"')

    def test_non_field_errors_are_covered_by_summary_and_body(self):
        form = EnrollmentForm(
            data={
                "display_name": "Ada Lovelace",
                "certificate_name": "",
                "display_on_leaderboard": "on",
                "display_public_profile": "on",
            },
            instance=self.enrollment,
            user=self.user,
        )
        self.assertTrue(form.is_valid())
        form.add_error(None, "The course team could not accept this save.")
        form.add_error("certificate_name", OVERLONG_ERROR)

        body = render_to_string(
            "courses/enrollment.html",
            {
                "form": form,
                "course": self.course,
                "course_family": self.course.course,
                "enrollment": self.enrollment,
            },
        )

        # The summary lists the non-field failure as plain text (nothing to
        # focus) and the field failure as a link; the body repeats both so
        # the explanation is not only in the summary.
        self.assertEqual(body.count("The course team could not accept this save."), 2)
        self.assertIn('<a href="#id_certificate_name">', body)
