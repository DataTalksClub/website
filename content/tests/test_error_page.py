"""The page-not-found page on design 5a (issue #179).

`templates/404.html` is the one page in the system that renders while something
has already gone wrong: `content.public_views._public_not_found` renders it with
no page context at all, and Django's own handler404 can render it under a URLconf
that does not hold every route the site normally has.  It therefore gets the
shared shell — a lost visitor still needs the navigation — but nothing that
depends on context a failed request may not have supplied.
"""

from __future__ import annotations

import re

from django.template.loader import render_to_string
from django.test import TestCase, override_settings
from django.urls import path, reverse

from core.views import liveness

RETIRED_ASSETS = (
    "/static/courses.css",
    "/static/core/site_shell.css",
    "/static/core/accessibility.css",
    "tailwindcss",
    "fontawesome",
)
TEMPLATE_SYNTAX = ("{#", "#}", "{%", "%}", "{{", "}}")

# A deliberately reduced URLconf: no home, no wiki, no dark-mode toggle.  Django's
# default handler404 renders `404.html` against whatever URLconf is in force, and
# an error page that cannot reverse a route must shorten rather than raise — a
# NoReverseMatch there turns a 404 into a 500.
urlpatterns = [path("health/live", liveness, name="health-live")]

# The heading is a slot in the shared text page (issue #179), so it is indented
# inside its element rather than written on one line.
HEADING = re.compile(r'<h1 id="text-page-heading">\s*Page not found\s*</h1>')


class MissingPageTests(TestCase):
    def setUp(self) -> None:
        self.response = self.client.get("/podwiki")
        self.assertEqual(self.response.status_code, 404)
        self.body = self.response.content.decode()

    def test_the_page_carries_one_inline_stylesheet_and_no_legacy_css(self) -> None:
        self.assertEqual(self.body.count("<style>"), 1)
        # The shared design system partial, not a fork of it.
        self.assertIn("--graph-edge:", self.body)
        self.assertEqual(re.findall(r'<link[^>]+rel="stylesheet"', self.body), [])
        for retired in RETIRED_ASSETS:
            self.assertNotIn(retired, self.body, retired)

    def test_the_page_includes_the_shared_shell_instead_of_copying_it(self) -> None:
        for partial in ("core/_site_shell_head.html", "core/_site_shell_foot.html"):
            self.assertTemplateUsed(self.response, partial)

    def test_the_page_leaks_no_unrendered_template_syntax(self) -> None:
        for token in TEMPLATE_SYNTAX:
            self.assertNotIn(token, self.body, token)

    def test_the_page_is_the_shared_text_page_rather_than_a_document_of_its_own(
        self,
    ) -> None:
        """A heading, a sentence and two ways back is a text page (issue #179)."""

        self.assertTemplateUsed(self.response, "public/text_page.html")

    def test_the_page_states_the_miss_and_offers_both_ways_back(self) -> None:
        self.assertRegex(self.body, HEADING)
        self.assertEqual(self.body.count("<h1"), 1)
        self.assertIn("We couldn't find that page.", self.body)
        self.assertIn(
            f'<a class="cta cta-primary" href="{reverse("home")}">Return home</a>',
            self.body,
        )
        self.assertIn(
            f'<a class="cta cta-secondary" href="{reverse("wiki-home")}">Browse the Wiki</a>',
            self.body,
        )

    def test_a_miss_is_not_a_destination_and_claims_no_canonical(self) -> None:
        self.assertNotIn('rel="canonical"', self.body)
        self.assertIn("<title>Page not found — DataTalks.Club</title>", self.body)

    def test_the_page_still_carries_the_navigation_a_lost_visitor_needs(self) -> None:
        self.assertIn('id="site-navigation-links"', self.body)
        self.assertIn(f'href="{reverse("slack")}"', self.body)

    def test_the_page_renders_without_any_page_context(self) -> None:
        """`_public_not_found` supplies no seo title, description or canonical."""

        body = render_to_string("404.html")

        self.assertRegex(body, HEADING)
        self.assertIn("<title>Page not found — DataTalks.Club</title>", body)

    @override_settings(ROOT_URLCONF=__name__)
    def test_the_page_renders_under_a_reduced_urlconf(self) -> None:
        response = self.client.get("/nothing-here")

        self.assertEqual(response.status_code, 404)
        body = response.content.decode()
        self.assertIn("Page not found", body)
        # The routes that cannot be reversed drop out of the page instead of raising.
        self.assertNotIn("Return home", body)
        self.assertNotIn("Browse the Wiki", body)
