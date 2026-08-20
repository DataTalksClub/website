"""The shared archive row: one partial behind every dated index on the site.

The blog, the books archive, the podcast index and every band of a person's
profile draw the same row — a date on a fixed rail, and the record beside it as a
card whose credit sits at its foot.  Each of them used to draw it in its own
template, and the row had to be converged by hand in four files at once.  These
tests hold the row to being one thing: the partial renders each of its slots and
omits the ones a record has no fact for, every surface goes through it, and no
page keeps a row shape of its own.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase

from core.templatetags.accessibility import counted

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ROW_TEMPLATE = "public/_archive_row.html"
DESIGN_SYSTEM = REPOSITORY_ROOT / "templates/core/_design_system.html"
CARD_LINK_STYLES = REPOSITORY_ROOT / "templates/public/_card_link_styles.html"
# Every template that draws the row, and the two event surfaces that deliberately
# do not (see the partial's own comment for why).
SURFACES = (
    REPOSITORY_ROOT / "templates/public/collection_hub.html",
    REPOSITORY_ROOT / "templates/public/podcast_hub.html",
    REPOSITORY_ROOT / "templates/public/_contribution_row.html",
)
EVENT_SURFACES = (
    REPOSITORY_ROOT / "templates/public/events.html",
    REPOSITORY_ROOT / "templates/core/home.html",
)
# The per-page row shapes the shared row replaced.  None of them may come back:
# a page that needs a different row needs the shared one to change.
RETIRED_ROW_RULES = (
    "record-card",
    "record-title",
    "record-summary",
    "record-authors",
    "record-date",
    "episode-card",
    "episode-title",
    "episode-summary",
    "episode-meta",
    "episode-date",
    "person-row-title",
    "person-row-body",
    "person-row-note",
    "person-row-date",
    "person-row-marks",
)


class CountedFilterTests(SimpleTestCase):
    """The row takes its pill as one piece of text, so something has to join
    a count to the noun it counts."""

    def test_a_count_is_named_in_the_words_a_reader_reads(self) -> None:
        self.assertEqual(counted(["a", "b"], "question"), "2 questions")
        self.assertEqual(counted(["a"], "question"), "1 question")
        self.assertEqual(counted(3, "event"), "3 events")

    def test_an_irregular_plural_is_given_rather_than_guessed(self) -> None:
        self.assertEqual(counted(2, "entry,entries"), "2 entries")
        self.assertEqual(counted(1, "entry,entries"), "1 entry")

    def test_nothing_to_count_says_nothing_at_all(self) -> None:
        nothing: tuple[object, ...] = ([], (), 0, "", None)
        for empty in nothing:
            with self.subTest(empty=empty):
                self.assertEqual(counted(empty, "question"), "")


class ArchiveRowSlotTests(SimpleTestCase):
    def render(self, **slots: object) -> str:
        return render_to_string(ROW_TEMPLATE, slots)

    def test_a_full_row_draws_every_slot_in_the_order_the_design_gives_them(self) -> None:
        body = self.render(
            row_title="How to ship it",
            row_url="/blog/how-to-ship-it.html",
            row_date="2025-08-11",
            row_mark="",
            row_eyebrow="guest",
            row_pill="2 questions",
            row_pill_variant="status-pill-mint",
            row_pill_extra="upcoming",
            row_summary="What actually worked, and what broke.",
            row_credits=[{"name": "Alexey Grigorev", "public_path": "/people/x.html"}],
            row_class="episode-row",
            row_hook="data-podcast-episode",
        )

        self.assertIn('class="list-row archive-row episode-row"', body)
        self.assertIn("data-podcast-episode", body)
        self.assertIn("<span>August 11</span>", body)
        self.assertIn("<span>2025</span>", body)
        self.assertIn('<time datetime="2025-08-11">', body)
        self.assertIn('class="card archive-card stretched-card-link"', body)
        self.assertIn('<p class="mono-label">guest</p>', body)
        self.assertIn(
            '<span class="status-pill status-pill-mint">2 questions</span>',
            body,
        )
        self.assertIn('<span class="status-pill">upcoming</span>', body)
        self.assertIn(
            '<a href="/blog/how-to-ship-it.html">How to ship it</a>',
            body,
        )
        self.assertIn('<p class="archive-summary">What actually worked, and what broke.</p>', body)
        self.assertIn('class="person-chips card-credit"', body)
        self.assertIn("Alexey Grigorev", body)

        # One h3 per row, and the title is the row's only link besides its credit.
        self.assertEqual(body.count("<h3"), 1)
        self.assertEqual(
            re.findall(r'<a [^>]*href="([^"]+)"', body),
            ["/blog/how-to-ship-it.html", "/people/x.html"],
        )
        # The credit is the foot of the card, under everything the card says.
        self.assertLess(body.index("archive-title"), body.index("card-credit"))
        self.assertLess(body.index("archive-summary"), body.index("card-credit"))
        # And it carries no "By"/"With" label: the face beside the name is the credit.
        self.assertNotIn("By ", body)
        self.assertNotIn("With ", body)

    def test_a_podcast_row_uses_a_plain_blue_eyebrow_and_keeps_credits_separate(self) -> None:
        body = self.render(
            row_title="How to ship it",
            row_url="/podcast/s24e6-how-to-ship-it.html",
            row_date="2025-08-11",
            row_mark="play",
            row_pill="Season 24 · Episode 6",
            row_pill_variant="status-pill-mint",
            row_summary="What actually worked, and what broke.",
            row_credits=[{"name": "Alexey Grigorev", "public_path": "/people/x.html"}],
        )

        self.assertIn('class="card archive-card stretched-card-link podcast-card"', body)
        self.assertIn(
            '<p class="mono-label mono-label-indigo podcast-meta">Season 24 · Episode 6</p>',
            body,
        )
        self.assertNotIn('class="status-pill status-pill-mint">Season 24 · Episode 6</span>', body)
        self.assertIn('class="play-disc"', body)
        self.assertIn(
            '<a href="/podcast/s24e6-how-to-ship-it.html">How to ship it</a>',
            body,
        )
        self.assertIn('<a class="band-link person-chip-name" href="/people/x.html">', body)
        self.assertEqual(
            re.findall(r'<a [^>]*href="([^"]+)"', body),
            ["/podcast/s24e6-how-to-ship-it.html", "/people/x.html"],
        )
        self.assertNotIn('role="link"', body)
        self.assertNotIn("onclick=", body)
        self.assertNotIn("onkeydown=", body)

    def test_a_row_draws_nothing_for_a_fact_its_record_does_not_carry(self) -> None:
        body = self.render(row_title="A quiet record", row_url="/blog/quiet.html")

        self.assertIn('class="list-row archive-row', body)
        self.assertIn("archive-row-undated", body)
        self.assertNotIn("date-rail", body)
        self.assertNotIn("<time", body)
        self.assertNotIn("play-disc", body)
        self.assertNotIn("mono-label", body)
        self.assertNotIn("status-pill", body)
        self.assertNotIn("archive-summary", body)
        self.assertNotIn("card-credit", body)
        self.assertIn('<a href="/blog/quiet.html">A quiet record</a>', body)

    def test_a_dated_row_keeps_its_rail_and_says_the_day_in_both_halves(self) -> None:
        # The books catalogue pads its day with a midnight the record does not
        # carry; the rail publishes the day, not a clock reading.
        body = self.render(
            row_title="Designing Data-Intensive Applications",
            row_url="/books/ddia.html",
            row_date="2025-10-06T00:00:00",
        )

        self.assertNotIn("archive-row-undated", body)
        self.assertIn('<time datetime="2025-10-06">', body)
        self.assertIn("<span>October 6</span>", body)
        self.assertIn("<span>2025</span>", body)
        self.assertNotIn("00:00", body)

    def test_a_pill_with_no_variant_is_the_plain_pill_and_not_an_empty_class(self) -> None:
        body = self.render(row_title="A book", row_url="/books/a.html", row_pill="12 questions")

        self.assertIn('<span class="status-pill">12 questions</span>', body)
        self.assertNotIn('class="status-pill "', body)


class ArchiveRowSurfaceTests(TestCase):
    def test_every_dated_index_draws_the_shared_row(self) -> None:
        for path in ("/blog", "/books", "/podcast", "/people/alexeygrigorev.html"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                body = response.content.decode()
                self.assertTemplateUsed(response, ROW_TEMPLATE)
                self.assertIn('class="list-row archive-row', body)
                self.assertIn('class="card archive-card stretched-card-link', body)
                # The row's own markup reaches the page rendered, never as source.
                self.assertNotIn("row_title=", body)

    def test_every_caller_passes_only_so_the_row_draws_what_it_was_given(self) -> None:
        includes = []
        for template in sorted((REPOSITORY_ROOT / "templates").rglob("*.html")):
            for line in template.read_text(encoding="utf-8").splitlines():
                if f'include "{ROW_TEMPLATE}"' in line and not line.lstrip().startswith("*"):
                    includes.append((template.name, line.strip()))

        self.assertTrue(includes)
        for name, line in includes:
            with self.subTest(template=name):
                self.assertTrue(line.endswith("only %}"), line)

    def test_no_page_keeps_a_row_shape_of_its_own(self) -> None:
        for template in (
            *SURFACES,
            REPOSITORY_ROOT / "templates/public/person_detail.html",
        ):
            source = template.read_text(encoding="utf-8")
            for retired in RETIRED_ROW_RULES:
                with self.subTest(template=template.name, rule=retired):
                    self.assertNotIn(retired, source)

    def test_the_event_card_is_drawn_once_for_the_two_pages_that_use_it(self) -> None:
        """The events index and the homepage draw one card, so it has one home."""

        design_system = DESIGN_SYSTEM.read_text(encoding="utf-8")
        self.assertIn(".event-card h3 a::after", design_system)
        self.assertIn(".event-summary {", design_system)
        for template in EVENT_SURFACES:
            with self.subTest(template=template.name):
                source = template.read_text(encoding="utf-8")
                self.assertNotIn(".event-card h3 a::after", source)
                self.assertNotIn(".event-summary {", source)
                self.assertNotIn(".event-card .kind", source)

    def test_public_card_link_styles_keep_the_shared_design_system_owned_by_sagan(self) -> None:
        styles = CARD_LINK_STYLES.read_text(encoding="utf-8")
        design_system = DESIGN_SYSTEM.read_text(encoding="utf-8")

        self.assertIn(".stretched-card-link .archive-title a::after", styles)
        self.assertIn(".stretched-card-link .person-chip a", styles)
        self.assertIn(".podcast-card {", styles)
        for declaration in ("background: transparent;", "border: 0;", "box-shadow: none;"):
            with self.subTest(declaration=declaration):
                self.assertIn(declaration, styles)
        self.assertNotIn("stretched-card-link", design_system)
