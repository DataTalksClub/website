"""Body call-to-action buttons written in Markdown (``core.markdown_actions``).

A standalone link marked ``{.button}`` / ``{.button-primary}`` renders as the
shared ``templates/core/_button.html`` control in every pipeline that renders a
synced body; any other marker, or a marker inside a sentence, leaves an
ordinary link and never shows the marker as text.
"""

from __future__ import annotations

from django.template.loader import render_to_string
from django.test import SimpleTestCase

from academy.templatetags.academy_text import academy_markdown
from content.article_content import prose_sections, render_body_markdown
from content.docs_rendering import render_docs_markdown
from content.projection_helpers import _plain_inline
from core.markdown_actions import is_button_line, strip_link_markers
from courses.registration import render_markdown as render_course_markdown

SECONDARY = (
    '<p class="prose-action"><a class="cta cta-secondary interactive-lift" '
    'href="https://example.invalid/subscribe" rel="noopener noreferrer">Subscribe</a></p>'
)


def _pipelines():
    return {
        "article": lambda text: render_body_markdown(text)[0],
        "academy": lambda text: str(academy_markdown(text)),
        "docs": lambda text: render_docs_markdown(text)[0],
        "course": render_course_markdown,
    }


class BodyButtonRenderingTests(SimpleTestCase):
    def test_a_marked_standalone_link_is_the_shared_secondary_action(self) -> None:
        source = "Intro.\n\n[Subscribe](https://example.invalid/subscribe){.button}\n\nAfter."
        for name, render in _pipelines().items():
            with self.subTest(name):
                html = render(source)
                if name == "course":
                    # Course Markdown opens every outside link in a new tab.
                    self.assertIn(
                        '<a class="cta cta-secondary interactive-lift" '
                        'href="https://example.invalid/subscribe" rel="noopener noreferrer" '
                        'target="_blank">Subscribe</a>',
                        html,
                    )
                else:
                    self.assertIn(SECONDARY, html)
                self.assertNotIn("{.", html)

    def test_the_primary_variant_is_explicit_and_a_site_link_gets_no_rel(self) -> None:
        for name, render in _pipelines().items():
            with self.subTest(name):
                html = render("[Join the Academy](/academy/){.button-primary}")
                self.assertIn(
                    '<a class="cta cta-primary interactive-lift" href="/academy/">'
                    "Join the Academy</a>",
                    html,
                )

    def test_the_control_is_the_shared_button_template(self) -> None:
        shared = render_to_string(
            "core/_button.html",
            {
                "href": "https://example.invalid/subscribe",
                "label": "Subscribe",
                "rel": "noopener noreferrer",
            },
        )
        self.assertIn(" ".join(shared.split()).replace(" >", ">"), SECONDARY)

    def test_an_unknown_marker_or_an_inline_marker_leaves_an_ordinary_link(self) -> None:
        sources = (
            "[Subscribe](https://example.invalid/subscribe){.button-red}",
            "[Subscribe](https://example.invalid/subscribe){.btn}",
            "Please [Subscribe](https://example.invalid/subscribe){.button} today.",
            "- [Subscribe](https://example.invalid/subscribe){.button}",
        )
        for name, render in _pipelines().items():
            for source in sources:
                with self.subTest(name=name, source=source):
                    html = render(source)
                    self.assertIn('href="https://example.invalid/subscribe"', html)
                    self.assertIn(">Subscribe</a>", html)
                    self.assertNotIn("cta", html)
                    self.assertNotIn("{.", html)

    def test_a_dangerous_destination_is_never_a_button(self) -> None:
        for name, render in _pipelines().items():
            with self.subTest(name):
                html = render("[Click](javascript:alert(1)){.button}")
                self.assertNotIn("cta", html)
                self.assertNotIn("javascript:", html)

    def test_the_label_is_escaped(self) -> None:
        html = str(academy_markdown("[a <b>x</b>](https://example.invalid/){.button}"))
        self.assertIn("a &lt;b&gt;x&lt;/b&gt;</a>", html)

    def test_a_marker_inside_code_stays_code(self) -> None:
        html = render_body_markdown("`[x](https://example.invalid/){.button}`")[0]
        self.assertIn("<code>[x](https://example.invalid/){.button}</code>", html)

    def test_plain_text_projections_read_the_label_only(self) -> None:
        source = "[Subscribe](https://example.invalid/subscribe){.button}"
        self.assertEqual(_plain_inline(source), "Subscribe")
        self.assertEqual(
            strip_link_markers(source), "[Subscribe](https://example.invalid/subscribe)"
        )
        self.assertTrue(is_button_line(source))
        self.assertFalse(is_button_line(f"See {source}"))


class ArticleBodyButtonPageTests(SimpleTestCase):
    def test_an_article_block_draws_the_button_in_the_reading_column(self) -> None:
        sections = prose_sections(
            (
                {"kind": "paragraph", "text": "Subscribe to get the rest:"},
                {
                    "kind": "paragraph",
                    "text": "Subscribe",
                    "markdown": "[Subscribe](https://example.invalid/subscribe){.button}",
                },
            )
        )
        page = render_to_string("public/_prose_blocks.html", {"sections": sections})
        self.assertIn(SECONDARY, page)
        self.assertNotIn("{.button}", page)
