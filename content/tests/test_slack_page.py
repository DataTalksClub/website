"""The Slack landing page on design 5a (issue #179).

`/slack` is one of the nine primary navigation entries and the community's own
front door, and it is the page the first shell copies quietly broke: five pages
lost the Slack link because each carried its own masthead.  The page now includes
the shared shell instead, so these tests pin what the rebuilt page must still
offer — the projected title, lede, every channel, and the one action it owns —
next to the design contract every page in the system carries.
"""

from __future__ import annotations

import re

from django.test import TestCase
from django.urls import reverse

from content.review_projection import review_projection

RETIRED_ASSETS = (
    "/static/courses.css",
    "/static/core/site_shell.css",
    "/static/core/accessibility.css",
    "tailwindcss",
    "fontawesome",
)
TEMPLATE_SYNTAX = ("{#", "#}", "{%", "%}", "{{", "}}")


class SlackPageTests(TestCase):
    def setUp(self) -> None:
        self.page = review_projection()["slack"]
        self.response = self.client.get(reverse("slack"))
        self.assertEqual(self.response.status_code, 200)
        self.body = self.response.content.decode()

    def test_the_page_carries_one_inline_stylesheet_and_no_legacy_css(self) -> None:
        self.assertEqual(self.body.count("<style>"), 1)
        # The shared design system partial, not a fork of it.
        self.assertIn("--graph-edge:", self.body)
        self.assertEqual(re.findall(r'<link[^>]+rel="stylesheet"', self.body), [])
        for retired in RETIRED_ASSETS:
            self.assertNotIn(retired, self.body, retired)

    def test_the_page_includes_the_shared_shell_instead_of_copying_it(self) -> None:
        """The regression that started the rebuild: a copied masthead loses links."""

        for partial in ("core/_site_shell_head.html", "core/_site_shell_foot.html"):
            self.assertTemplateUsed(self.response, partial)

    def test_the_page_is_the_shared_text_page_rather_than_a_document_of_its_own(
        self,
    ) -> None:
        """A heading and some prose is a text page, so `/slack` is one (issue #179)."""

        self.assertTemplateUsed(self.response, "public/text_page.html")

    def test_the_page_leaks_no_unrendered_template_syntax(self) -> None:
        for token in TEMPLATE_SYNTAX:
            self.assertNotIn(token, self.body, token)

    def test_the_navigation_marks_slack_as_the_page_the_reader_is_on(self) -> None:
        self.assertRegex(
            self.body,
            r'href="' + re.escape(reverse("slack")) + r'"\s+aria-current="page"\s+>Slack</a>',
        )

    def test_the_hero_truthfully_describes_the_available_slack_help(self) -> None:
        # The heading and the standfirst are the shared text page's own slots, so
        # they carry its identifier and its `.prose-lede` face (issue #179).
        self.assertRegex(
            self.body,
            r'<h1 id="text-page-heading">\s*DataTalks\.Club on Slack\s*</h1>',
        )
        self.assertRegex(
            self.body,
            r'<p class="prose-lede">\s*See where DataTalks\.Club members talk, and contact '
            r"the community team if you need help with the next step\.\s*</p>",
        )
        self.assertEqual(self.body.count("<h1"), 1)

    def test_every_channel_is_offered_as_a_chip_holding_a_mono_literal(self) -> None:
        """A channel name is a literal, so it keeps the system's `.mono-code` face."""

        self.assertIn('<h2 id="slack-channels-heading">Find your conversation</h2>', self.body)
        channels = self.page["channels"]
        self.assertTrue(channels)
        for channel in channels:
            with self.subTest(channel=channel):
                self.assertIn(f'<code class="mono-code">{channel}</code>', self.body)
        self.assertEqual(self.body.count('class="chip chip-plain channel"'), len(channels))

    def test_the_page_keeps_its_one_action_as_a_call_to_action(self) -> None:
        self.assertIn('<h2 id="slack-help-heading">Need help with Slack?</h2>', self.body)
        self.assertIn(
            "Contact the community team and they will reply with the appropriate next step.",
            self.body,
        )
        self.assertRegex(
            self.body,
            r'<a\s+class="cta cta-primary"\s+href="'
            + re.escape(str(self.page["troubleshooting_url"]))
            + r'"\s+target="_blank"\s+rel="noreferrer"\s*>\s*Contact the community team',
        )
        self.assertIn('<span class="sr-only">(opens in a new tab)</span>', self.body)
        self.assertLess(
            self.body.index("Contact the community team"),
            self.body.index('id="slack-channels-heading"'),
        )
