"""The band-ground rule: a hero keeps cream, everything after it takes lavender.

The rule had been applied by hand, page by page, three times, and each new page
rediscovered it.  This test is the written-down version of the section "Which
ground a band takes" in `_docs/design/design-5a.md`: it reads the band sequence
straight out of every public page template and fails when a page invents its own
alternation.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.test import SimpleTestCase

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

BAND_PATTERN = re.compile(r'class="band (band-[a-z]+)')

# The page background token, as a page's own stylesheet block sets it.
PAGE_TOKEN_PATTERN = re.compile(r":root\s*\{[^}]*--page:\s*var\(--lavender\);")

# The first thing a page draws below the seam.  A page whose body slid back up
# into the warm hero would put one of these ahead of its first lavender band,
# which a band sequence alone cannot see: the hero would still be cream and the
# content band would still be lavender while the reading sat on the wrong one.
BODIES_BELOW_THE_SEAM: dict[str, str] = {
    "templates/public/article_detail.html": '<div class="prose prose-reading article-body">',
    "templates/public/book_detail.html": '<div class="prose book-summary">',
    "templates/public/collection_hub.html": '<div class="row-list collection-rows"',
    "templates/public/event_detail.html": '<div class="prose prose-reading event-description">',
    "templates/public/events.html": '<div class="row-list event-rows"',
    "templates/public/person_detail.html": '<div class="prose person-bio">',
    "templates/public/podcast_detail.html": '<p class="mono-note episode-published">',
    "templates/public/podcast_hub.html": '<div class="row-list" data-podcast-season=',
    "templates/public/text_page.html": '<div class="prose prose-reading text-page-body">',
    "templates/public/wiki_detail.html": '<div class="prose wiki-prose">',
    "templates/review/faq_home.html": '<div class="row-list faq-rows">',
    "templates/review/registration_preview.html": 'aria-labelledby="registration-state-heading"',
    "templates/core/content_page.html": "{% block page_content %}",
    # Authentication pages share their bands through auth_page.html; the child
    # supplies the ways in through the parent's content block.
    "accounts/templates/account/auth_page.html": "{% block auth_content %}",
}

# Every public reading surface the rule governs: the editorial pages, the review
# surfaces behind /docs, /faq and the cohort previews, the two course pages the
# masthead's Courses entry leads to, and the account entrance family a
# signed-out visitor meets — sign up, sign in, sign out, the password reset
# steps and the provider outcomes.  The entrance pages are where the homepage's
# primary call to action lands, so a ground that alternates on its own there
# reads as a different site at exactly the wrong moment.
#
# `socialaccount/connections.html` is deliberately not here yet: it is an
# account *management* page rather than an entrance, and it draws cream, cream,
# lavender.  Moving its second band is its own change, not a drive-by inside a
# sign-up redesign.
GOVERNED_TEMPLATES: tuple[Path, ...] = (
    *sorted((REPOSITORY_ROOT / "templates/public").glob("*.html")),
    *sorted((REPOSITORY_ROOT / "templates/review").glob("*.html")),
    REPOSITORY_ROOT / "courses/templates/courses/course_list.html",
    REPOSITORY_ROOT / "courses/templates/courses/course.html",
    *sorted((REPOSITORY_ROOT / "accounts/templates/account").glob("*.html")),
    REPOSITORY_ROOT / "accounts/templates/accounts/login.html",
    REPOSITORY_ROOT / "course_platform_templates/account/logout.html",
    REPOSITORY_ROOT / "course_platform_templates/socialaccount/authentication_error.html",
    REPOSITORY_ROOT / "course_platform_templates/socialaccount/identity_conflict.html",
    REPOSITORY_ROOT / "course_platform_templates/socialaccount/login_cancelled.html",
    REPOSITORY_ROOT / "course_platform_templates/socialaccount/signup.html",
)

# The homepage is composed directly from its own mockup and alternates cream,
# lavender, mint and ink deliberately; it is the one page in the system that is
# allowed to.  The course platform's task surfaces (dashboard, homework,
# leaderboard, submissions, statistics, enrolment, registration, wrapped) and
# Studio came from the course-platform adoption rather than from the editorial
# system and are not governed here.
UNGOVERNED = (
    REPOSITORY_ROOT / "templates/core/home.html",
    REPOSITORY_ROOT / "templates/studio/base.html",
)


def band_sequence(template: Path) -> tuple[str, ...]:
    """Return the ground of each band the template draws, in source order."""
    return tuple(BAND_PATTERN.findall(template.read_text(encoding="utf-8")))


class BandGroundTests(SimpleTestCase):
    def test_every_public_page_puts_its_hero_on_cream(self) -> None:
        for template in GOVERNED_TEMPLATES:
            bands = band_sequence(template)
            if not bands:
                continue
            with self.subTest(template=template.name):
                self.assertEqual(
                    bands[0],
                    "band-cream",
                    "The first band on a page is the hero and keeps the warm cream "
                    "ground the masthead sits on.",
                )

    def test_every_band_after_a_hero_takes_the_content_ground(self) -> None:
        for template in GOVERNED_TEMPLATES:
            bands = band_sequence(template)
            with self.subTest(template=template.name):
                self.assertEqual(
                    [ground for ground in bands[1:] if ground != "band-lavender"],
                    [],
                    "Lavender is the content ground: every band after the hero takes "
                    'it.  See "Which ground a band takes" in _docs/design/design-5a.md.',
                )

    def test_no_public_page_draws_a_mint_or_ink_band(self) -> None:
        # Mint marks an events *section* inside a page about several things, and
        # ink closes a page with a call to action.  Only the homepage does either.
        for template in GOVERNED_TEMPLATES:
            with self.subTest(template=template.name):
                grounds = set(band_sequence(template))
                self.assertNotIn("band-mint", grounds)
                self.assertNotIn("band-ink", grounds)

    def test_a_page_that_ends_cool_carries_the_page_token(self) -> None:
        # `--page` is the body background, and the strip below the last band —
        # the analytics dialog's ground, and everything under the footer — is
        # drawn with it.  A page whose last band is the content ground has to
        # move the token with it, or that strip snaps back to cream between the
        # lavender it continues and the cream footer.
        for template in GOVERNED_TEMPLATES:
            bands = band_sequence(template)
            if not bands or bands[-1] != "band-lavender":
                continue
            with self.subTest(template=template.name):
                self.assertIsNotNone(
                    PAGE_TOKEN_PATTERN.search(template.read_text(encoding="utf-8")),
                    f"{template.name} ends on the content ground but never sets "
                    "`--page` to `var(--lavender)` on `:root` in its own stylesheet "
                    "block, so the tail below its last band snaps back to cream.",
                )

    def test_the_body_of_a_page_sits_below_the_seam(self) -> None:
        # The warm band marks where a page starts; it is not the page.  Prose,
        # cards, list rows, a detail body and a state panel are all content, so
        # they belong under the seam even when the hero above them is short.
        for relative, body in BODIES_BELOW_THE_SEAM.items():
            template = REPOSITORY_ROOT / relative
            source = template.read_text(encoding="utf-8")
            with self.subTest(template=template.name):
                self.assertIn(body, source, "The pinned body markup has been renamed.")
                seam = source.find('class="band band-lavender')
                self.assertNotEqual(seam, -1, "The page draws no content band.")
                self.assertGreater(
                    source.find(body),
                    seam,
                    "This page's body reads on the content ground, not in the warm "
                    'hero.  See "Which ground a band takes" in _docs/design/design-5a.md.',
                )

    def test_signup_uses_the_shared_auth_content_band(self) -> None:
        signup = REPOSITORY_ROOT / "accounts/templates/account/signup.html"
        source = signup.read_text(encoding="utf-8")
        self.assertIn('{% extends "account/auth_page.html" %}', source)
        self.assertIn("account/_social_provider_choices.html", source)

    def test_course_uses_the_shared_content_page_band(self) -> None:
        course = REPOSITORY_ROOT / "courses/templates/courses/course.html"
        source = course.read_text(encoding="utf-8")
        self.assertIn('{% extends "core/content_page.html" %}', source)
        self.assertIn("{% block page_content %}", source)

    def test_docs_use_the_shared_content_page_band(self) -> None:
        for name in ("docs_home.html", "docs_detail.html"):
            with self.subTest(template=name):
                source = (REPOSITORY_ROOT / "templates/review" / name).read_text()
                self.assertIn('{% extends "core/content_page.html" %}', source)
                self.assertIn("{% block page_content %}", source)

    def test_the_homepage_keeps_its_own_alternation(self) -> None:
        # Guards the exception itself: the homepage is drawn from its own mockup and
        # is the one page allowed to alternate, so flattening it has to be a decision
        # rather than a drive-by.  The sequence is not pinned exactly - a homepage
        # band may still be added or removed - only the alternation it exists for.
        bands = band_sequence(REPOSITORY_ROOT / "templates/core/home.html")

        self.assertEqual(bands[0], "band-cream")
        self.assertEqual(bands[-1], "band-ink", "The homepage closes on the ink CTA band.")
        self.assertGreaterEqual(
            len(set(bands)),
            3,
            "The homepage alternates grounds on purpose; see the band system section "
            "of _docs/design/design-5a.md.",
        )

    def test_the_governed_set_and_the_exceptions_do_not_overlap(self) -> None:
        for template in UNGOVERNED:
            with self.subTest(template=template.name):
                self.assertTrue(template.exists())
                self.assertNotIn(template, GOVERNED_TEMPLATES)
