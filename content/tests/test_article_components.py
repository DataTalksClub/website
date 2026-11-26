"""The named elements an article body places with a fenced block."""

from __future__ import annotations

from django.test import SimpleTestCase

from content import projection_helpers as builder
from content.article_content import prose_sections


def _blocks(body: str) -> list[dict]:
    return builder._article_blocks(body, media_root=None, counters={})


class ArticleComponentTests(SimpleTestCase):
    def test_a_named_fence_becomes_its_element_with_its_fields_checked(self) -> None:
        blocks = _blocks(
            "```facts\nlabel: Key facts\nitems:\n  - label: Seats\n    value: 15\n```\n"
        )
        self.assertEqual(
            blocks,
            [
                {
                    "kind": "component",
                    "component": "facts",
                    "label": "Key facts",
                    "items": [{"label": "Seats", "value": "15"}],
                }
            ],
        )

    def test_any_other_fence_stays_a_code_sample(self) -> None:
        blocks = _blocks("```python\nfacts = 1\n```\n")
        self.assertEqual(blocks, [{"kind": "code", "text": "facts = 1", "language": "python"}])

    def test_a_malformed_element_fails_the_sync(self) -> None:
        for body in (
            "```facts\nlabel: Key facts\n```\n",
            "```cards\nitems:\n  - title: Only a title\n```\n",
            "```callout\nvariant: loud\nbody: Hi.\n```\n",
            "```steps\nitems: []\n```\n",
            "```cards\nitems:\n  - title: T\n    body: B\n    link:\n"
            "      label: L\n      href: javascript:alert(1)\n```\n",
            "```facts\n: [unbalanced\n```\n",
        ):
            with self.subTest(body=body), self.assertRaises(builder.ProjectionBuildError):
                _blocks(body)

    def test_markdown_fields_are_rendered_and_sanitized_for_the_page(self) -> None:
        blocks = _blocks(
            "```callout\nbody: 'A [link](/academy/) and <script>x</script>'\n```\n"
            "```cards\nitems:\n  - title: T\n    body: '- one\n\n      - two'\n```\n"
        )
        callout, cards = (section.component for section in prose_sections(blocks))
        assert callout is not None and cards is not None
        self.assertEqual(callout["variant"], "info")
        self.assertIn('<a href="/academy/">link</a>', callout["body_html"])
        self.assertNotIn("<script>", callout["body_html"])
        self.assertIn("<li>one</li>", cards["items"][0]["body_html"])
