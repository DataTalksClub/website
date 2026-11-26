"""A guide's or recap's "## FAQ" section is drawn by the shared FAQ block.

The Markdown body stays as written upstream; `split_body_faq` lifts the
section out at render time so its questions render through core/_faq.html and
publish FAQPage data, and leaves anything that is not a clean list of
"### question" + answer untouched.
"""

from __future__ import annotations

from django.test import SimpleTestCase

from academy.presentation import split_body_faq

BODY = """Intro paragraph.

## FAQ

### Is it free?

Yes. See [the page](/academy/).

### Can I learn both?

Yes.

Second paragraph.
"""


class SplitBodyFaqTests(SimpleTestCase):
    def test_the_faq_section_becomes_items(self) -> None:
        split = split_body_faq(BODY)
        self.assertEqual(split["body"], "Intro paragraph.\n")
        self.assertEqual(split["heading"], "FAQ")
        self.assertEqual(
            [item["question"] for item in split["items"]], ["Is it free?", "Can I learn both?"]
        )
        self.assertIn('<a href="/academy/">the page</a>', split["items"][0]["answer_html"])
        self.assertIn("<p>Second paragraph.</p>", split["items"][1]["answer_html"])
        self.assertEqual(split["body_after"], "")

    def test_a_following_section_stays_prose_after_the_faq(self) -> None:
        split = split_body_faq(BODY + "\n## Further reading\n\nMore.\n")
        self.assertEqual(len(split["items"]), 2)
        self.assertEqual(split["body_after"], "## Further reading\n\nMore.")

    def test_a_body_without_a_clean_faq_section_is_unchanged(self) -> None:
        for body in (
            "Just prose.\n",
            "## FAQ\n\nSome text before any question.\n\n### Q?\n\nA.\n",
            "## FAQ\n\n### A question with no answer?\n",
            "```\n## FAQ\n### Q?\nA.\n```\n",
        ):
            with self.subTest(body=body):
                split = split_body_faq(body)
                self.assertEqual(split["items"], [])
                self.assertEqual(split["body"], body)
