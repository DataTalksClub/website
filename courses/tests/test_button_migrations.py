"""Focused source contracts for ordinary shared CTA/button migrations."""

from pathlib import Path

from django.test import SimpleTestCase

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def read_template(relative_path: str) -> str:
    return (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")


MIGRATED_TEMPLATES = (
    "courses/templates/courses/course.html",
    "courses/templates/courses/leaderboard.html",
    "courses/templates/courses/leaderboard_complaint.html",
    "courses/templates/courses/leaderboard_score_breakdown.html",
    "courses/templates/courses/register.html",
    "courses/templates/courses/user_wrapped.html",
    "courses/templates/courses/wrapped.html",
    "courses/templates/include/learning_in_public_links.html",
    "templates/404.html",
    "templates/management_api/credential_fixture.html",
    "templates/management_api/credential_fixture_away.html",
    "templates/public/_wiki_search_form.html",
    "templates/public/bad_request.html",
    "templates/public/book_detail.html",
    "templates/public/wiki_hub.html",
    "templates/public/wiki_special.html",
)


class SharedButtonMigrationTests(SimpleTestCase):
    def test_eligible_templates_delegate_actions_to_the_shared_include(self) -> None:
        for relative_path in MIGRATED_TEMPLATES:
            with self.subTest(template=relative_path):
                self.assertIn(
                    '{% include "core/_button.html"',
                    read_template(relative_path),
                )

    def test_special_controls_keep_their_explicit_behavior_seams(self) -> None:
        registration = read_template("courses/templates/courses/register.html")
        score_breakdown = read_template(
            "courses/templates/courses/leaderboard_score_breakdown.html"
        )

        self.assertIn("data-registration-submit", registration)
        self.assertIn('data-busy-label="Registering…"', registration)
        self.assertIn("data-flag-url=", score_breakdown)
        self.assertIn("data-flag-close", score_breakdown)
