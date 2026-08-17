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

# Every public reading surface the rule governs: the editorial pages, the review
# surfaces behind /docs, /faq and the cohort previews, and the two course pages
# the masthead's Courses entry leads to.
GOVERNED_TEMPLATES: tuple[Path, ...] = (
    *sorted((REPOSITORY_ROOT / "templates/public").glob("*.html")),
    *sorted((REPOSITORY_ROOT / "templates/review").glob("*.html")),
    REPOSITORY_ROOT / "courses/templates/courses/course_list.html",
    REPOSITORY_ROOT / "courses/templates/courses/course.html",
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
