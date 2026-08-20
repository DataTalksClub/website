"""The learner content ground stays lavender after each page's warm hero."""

from __future__ import annotations

import re
from pathlib import Path

from django.test import SimpleTestCase

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BAND_PATTERN = re.compile(r'class="band (band-[a-z]+)')
PAGE_TOKEN_PATTERN = re.compile(r":root\s*\{\s*--page:\s*var\(--lavender\);\s*\}")

# These are the rendered course-platform pages that had a cream content band
# after their hero. The hero remains the warm entry point; every later content
# band, including conditional sections, uses the lavender ground.
PAGE_BANDS = {
    "courses/templates/courses/dashboard.html": (
        "band-cream",
        "band-lavender",
        "band-lavender",
        "band-lavender",
        "band-lavender",
        "band-lavender",
        "band-lavender",
        "band-lavender",
    ),
    "courses/templates/courses/leaderboard_score_breakdown.html": (
        "band-cream",
        "band-lavender",
        "band-lavender",
    ),
    "courses/templates/courses/register.html": (
        "band-cream",
        "band-lavender",
        "band-lavender",
    ),
    "courses/templates/courses/user_wrapped.html": (
        "band-cream",
        "band-lavender",
        "band-ink",
        "band-lavender",
        "band-lavender",
        "band-lavender",
        "band-lavender",
        "band-ink",
    ),
    "courses/templates/courses/wrapped.html": (
        "band-cream",
        "band-lavender",
        "band-ink",
        "band-lavender",
        "band-lavender",
        "band-lavender",
        "band-lavender",
        "band-lavender",
        "band-ink",
    ),
    "courses/templates/homework/stats.html": (
        "band-cream",
        "band-lavender",
        "band-lavender",
        "band-lavender",
        "band-lavender",
    ),
}


class CourseContentSurfaceDesignTests(SimpleTestCase):
    def test_changed_pages_keep_a_cream_hero_and_lavender_content(self) -> None:
        for relative_path, expected_bands in PAGE_BANDS.items():
            source = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")
            bands = BAND_PATTERN.findall(source)
            with self.subTest(page=relative_path):
                self.assertEqual(bands, list(expected_bands))
                self.assertTrue(PAGE_TOKEN_PATTERN.search(source))
                self.assertNotIn("band-cream", bands[1:])

    def test_registration_keeps_the_attention_surface_and_posted_form(self) -> None:
        source = (REPOSITORY_ROOT / "courses/templates/courses/register.html").read_text(
            encoding="utf-8"
        )

        self.assertIn("background: var(--sand);", source)
        self.assertIn(
            '<form method="post" class="registration-form" data-registration-form>',
            source,
        )
        self.assertIn("{% csrf_token %}", source)
