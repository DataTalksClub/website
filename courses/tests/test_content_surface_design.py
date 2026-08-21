"""The learner content ground stays lavender after each page's warm hero."""

from __future__ import annotations

from pathlib import Path

from django.test import SimpleTestCase

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
WRAPPED_PAGES = (
    "courses/templates/courses/user_wrapped.html",
    "courses/templates/courses/wrapped.html",
)


class CourseContentSurfaceDesignTests(SimpleTestCase):
    def test_wrapped_pages_inherit_the_shared_content_surface(self) -> None:
        parent = (REPOSITORY_ROOT / "templates/core/content_page.html").read_text(
            encoding="utf-8"
        )
        self.assertIn('class="band band-cream content-page-header', parent)
        self.assertIn('class="band band-lavender content-page-content', parent)
        self.assertIn('class="shell content-shell"', parent)

        for relative_path in WRAPPED_PAGES:
            source = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")
            with self.subTest(page=relative_path):
                self.assertIn('{% extends "core/content_page.html" %}', source)
                self.assertNotIn('class="band band-cream', source)
                self.assertNotIn('class="band band-lavender', source)

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
