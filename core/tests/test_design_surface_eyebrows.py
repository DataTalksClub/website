"""Source contracts for the first design-surface eyebrow cleanup batch."""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

REMOVED_PAGE_EYEBROWS = {
    "templates/review/faq_detail.html": ('<p class="mono-label mono-label-indigo">Course FAQ</p>'),
    "templates/studio/event_identity_detail.html": "Studio · Event identity",
    "studio_courses/templates/studio_courses/course_list.html": (
        '<p class="mono-label mono-label-indigo">Studio</p>'
    ),
    "studio_courses/templates/studio_courses/course_admin.html": (
        '<p class="mono-label mono-label-indigo">Studio · Courses</p>'
    ),
    "studio_courses/templates/studio_courses/datamailer_operations.html": (
        '<p class="mono-label mono-label-indigo">Studio · Operations</p>'
    ),
    "studio_courses/templates/studio_courses/campaign_form.html": (
        '<p class="mono-label mono-label-indigo">Registration campaign</p>'
    ),
    "studio_courses/templates/studio_courses/enrollment_edit.html": (
        '<p class="mono-label mono-label-indigo">{{ course.title }}</p>'
    ),
    "studio_courses/templates/studio_courses/homework_submissions.html": (
        '<p class="mono-label mono-label-indigo">{{ course.title }}</p>'
    ),
    "studio_courses/templates/studio_courses/homework_submission_edit.html": (
        '<p class="mono-label mono-label-indigo">{{ homework.title }}</p>'
    ),
    "studio_courses/templates/studio_courses/project_submissions.html": (
        '<p class="mono-label mono-label-indigo">{{ course.title }}</p>'
    ),
    "studio_courses/templates/studio_courses/project_submission_edit.html": (
        '<p class="mono-label mono-label-indigo">{{ project.title }}</p>'
    ),
}

ACCOUNT_EYEBROW_TEMPLATES = (
    "accounts/templates/account/account_inactive.html",
    "accounts/templates/account/password_reset.html",
    "accounts/templates/account/password_reset_done.html",
    "accounts/templates/account/password_reset_from_key.html",
    "accounts/templates/account/password_reset_from_key_done.html",
    "accounts/templates/account/signup_closed.html",
    "course_platform_templates/account/logout.html",
    "course_platform_templates/socialaccount/authentication_error.html",
    # socialaccount/connections.html is gone: sign-in methods are a section of
    # account settings, and /accounts/3rdparty/ redirects into it.
    "course_platform_templates/socialaccount/login_cancelled.html",
    "course_platform_templates/socialaccount/signup.html",
)
GENERIC_ACCOUNT_EYEBROW = '<p class="mono-label mono-label-indigo">Account</p>'


class DesignSurfaceEyebrowSourceTests(SimpleTestCase):
    def template_source(self, relative_path: str) -> str:
        return (Path(settings.BASE_DIR) / relative_path).read_text()

    def test_redundant_review_and_studio_eyebrows_are_removed(self) -> None:
        for relative_path, snippet in REMOVED_PAGE_EYEBROWS.items():
            with self.subTest(template=relative_path):
                self.assertNotIn(snippet, self.template_source(relative_path))

    def test_generic_account_eyebrows_are_removed_from_noncanonical_states(self) -> None:
        for relative_path in ACCOUNT_EYEBROW_TEMPLATES:
            with self.subTest(template=relative_path):
                self.assertNotIn(
                    GENERIC_ACCOUNT_EYEBROW,
                    self.template_source(relative_path),
                )

    def test_useful_context_and_canonical_signup_framing_remain(self) -> None:
        retained_labels = {
            "accounts/templates/account/signup.html": GENERIC_ACCOUNT_EYEBROW,
            "course_platform_templates/socialaccount/identity_conflict.html": "Account safety",
            "templates/review/faq_home.html": "faq · {{ faq_courses|length }} course",
            "templates/review/docs_home.html": (
                "docs · {{ docs_total_guides }} guide{{ docs_total_guides|pluralize }}"
            ),
            "studio_courses/templates/studio_courses/campaign_registrations.html": (
                "Registration campaign"
            ),
            "studio_courses/templates/studio_courses/cloudwatch_dashboard.html": (
                "Studio · Observability"
            ),
            "studio_courses/templates/studio_courses/enrollments.html": "Student support",
        }

        for relative_path, label in retained_labels.items():
            with self.subTest(template=relative_path):
                self.assertIn(label, self.template_source(relative_path))
