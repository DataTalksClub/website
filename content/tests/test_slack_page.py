"""The Slack landing page on the design system (issue #179).

`/slack` is one of the nine primary navigation entries and the community's own
front door, and it is the page the first shell copies quietly broke: five pages
lost the Slack link because each carried its own masthead.  The page now includes
the shared shell instead, so these tests pin what the rebuilt page must still
offer -- the hero, the benefits with their channels, the joining steps, the FAQ
and the one action it owns, all read from the page's own synced row -- next to
the design contract every page in the system carries.
"""

from __future__ import annotations

import json
import re
from typing import Any

from django.test import TestCase
from django.urls import reverse
from django.utils.html import escape

from content.models import SyncedDocument

RETIRED_ASSETS = (
    "/static/courses.css",
    "/static/core/site_shell.css",
    "/static/core/accessibility.css",
    "tailwindcss",
    "fontawesome",
)
TEMPLATE_SYNTAX = ("{#", "#}", "{%", "%}", "{{", "}}")


def _synthetic_page() -> dict[str, Any]:
    from test_support.reference_data import SLACK_PAGE

    page: dict[str, Any] = json.loads(SLACK_PAGE.read_text(encoding="utf-8"))["page"]
    return page


class SlackPageAbsentTests(TestCase):
    """The page is its database row, so a database without one has no page."""

    def test_the_page_is_absent_rather_than_empty_when_no_row_publishes_it(self) -> None:
        SyncedDocument.objects.filter(content_kind="slack_page").delete()

        self.assertEqual(self.client.get(reverse("slack")).status_code, 404)


class SlackPageTests(TestCase):
    def setUp(self) -> None:
        # The synced row the parser writes; the assertions read what it says
        # rather than restating it. Re-seed from empty so a page that hides in
        # the shared reference data cannot pass for the row's own presence.
        SyncedDocument.objects.filter(
            source__slug="dtc-content", content_kind__in=("podcast_platforms", "slack_page")
        ).delete()
        from test_support.reference_data import load_synced_site_pages

        load_synced_site_pages()
        self.page = _synthetic_page()
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

    def test_the_page_leaks_no_unrendered_template_syntax(self) -> None:
        for token in TEMPLATE_SYNTAX:
            self.assertNotIn(token, self.body, token)

    def test_the_navigation_marks_slack_as_the_page_the_reader_is_on(self) -> None:
        self.assertRegex(
            self.body,
            r'href="' + re.escape(reverse("slack")) + r'"\s+aria-current="page"\s+>Slack</a>',
        )

    def test_the_hero_states_the_title_lead_proof_and_the_join_action(self) -> None:
        self.assertRegex(
            self.body, r"<h1>\s*" + re.escape(escape(str(self.page["title"]))) + r"\s*</h1>"
        )
        self.assertIn(escape(str(self.page["lead"])), self.body)
        self.assertIn(escape(str(self.page["proof"])), self.body)
        self.assertEqual(self.body.count("<h1"), 1)

    def test_one_join_action_repeats_at_hero_middle_and_close(self) -> None:
        """One goal: the same primary button, same label and destination, three times."""

        joins = re.findall(
            r'<a class="cta cta-primary interactive-lift" href="/community/slack/">Join Slack</a>',
            self.body,
        )
        # Hero and close always; the middle one sits under "Who's there",
        # which draws only when the survey record is published.
        self.assertGreaterEqual(len(joins), 2)
        main = self.body[self.body.index("<main") : self.body.index("</main>")]
        self.assertEqual(main.count("cta-primary"), len(joins))

    def test_every_benefit_channel_is_a_chip_holding_a_mono_literal(self) -> None:
        """A channel name is a literal, so it keeps the system's `.mono-code` face."""

        channels = [channel for benefit in self.page["benefits"] for channel in benefit["channels"]]
        self.assertTrue(channels)
        for channel in channels:
            with self.subTest(channel=channel):
                self.assertIn(f'<code class="mono-code">{channel}</code>', self.body)
        self.assertEqual(self.body.count('class="chip chip-plain channel"'), len(channels))
        for benefit in self.page["benefits"]:
            self.assertIn(escape(benefit["title"]), self.body)

    def test_the_benefits_link_to_the_slack_guide_in_the_docs(self) -> None:
        guide = reverse("docs-page", args=["general/slack/"])
        self.assertEqual(guide, "/docs/general/slack/")
        self.assertIn(
            f'<a class="band-link slack-guide-link" href="{guide}">Read the Slack guide', self.body
        )

    def test_the_three_joining_steps_and_the_faq_render(self) -> None:
        for step in self.page["steps"]:
            self.assertIn(escape(step["title"]), self.body)
        self.assertEqual(self.body.count('class="card slack-step"'), 3)
        # FAQ answers are Markdown, rendered to sanitized links.
        self.assertIn(escape(self.page["faq"][0]["question"]), self.body)
        self.assertIn('<a href="https://example.invalid/troubleshooting">help form</a>', self.body)

    def test_the_page_draws_no_eyebrow(self) -> None:
        main = self.body[self.body.index("<main") : self.body.index("</main>")]
        self.assertNotIn("kicker", main)
        self.assertNotIn("mono-label", main)
