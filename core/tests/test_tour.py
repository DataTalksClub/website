"""The community tour page and the site-wide tour stripe.

``/tour`` is a newcomer's walkthrough, deliberately distinct from the
homepage: it states the community's real scope from the catalogue's own
counts, shows only a teaser of the two sections a newcomer needs to see for
themselves (courses, events), and fills the rest with real, checked
editorial content the homepage never carries -- the founder's own origin
and why-it's-free story, the cohort-logistics facts from the community's own
docs, a second graduate quote on how it differs from a bootcamp, the 2025
community survey's own numbers, and the community guidelines' own norms.
Every claim here is a catalogue count or a sourced quote/fact -- never
invented copy -- so an empty database renders the page with the
database-backed claims dropped while the sourced editorial content still
renders.

The page is also the site's pitch, so it is drawn like the two pages that
already pitch: an illustrated hero, opening stat tiles, three chapters on the
solid rule at the breakout width with narrow subsections hanging under them,
and one ink moment at the close.  The tests below read that composition,
because a page whose content was right and whose drawing was not is exactly
what this page was before.  The black stripe above the footer advertises the
tour on every shared-shell page except the homepage (which ends with its own
closing band) and the tour itself.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

from community_base.content_sync.models import ContentSource as EngineContentSource
from django.test import TestCase
from django.urls import reverse

from content import catalogue
from test_support.course_catalog import build_reviewed_catalog

REPO_ROOT = Path(__file__).resolve().parents[2]
UPCOMING = (
    {
        "title": "Synthetic Office Hours",
        "public_path": "/events/synthetic-office-hours",
        "starts_at": "2026-10-01T18:00:00+00:00",
        "display_date": "Oct 1, 2026",
        "display_clock": "20:00 CEST",
    },
)


def _event_groups():
    upcoming: list = list(UPCOMING)
    recent: list = []

    class Groups:
        upcoming: list
        recent: list

    groups = Groups()
    groups.upcoming = upcoming
    groups.recent = recent
    return groups


class TourPageTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        build_reviewed_catalog()

    def _get(self):
        response = self.client.get(reverse("tour"))
        self.assertEqual(response.status_code, 200)
        return response

    def test_tour_renders_the_catalogue_from_the_database(self) -> None:
        body = self._get().content.decode()

        self.assertIn("Take the tour", body)
        self.assertIn("AI Dev Tools Zoomcamp", body)
        self.assertIn('rel="canonical" href="https://datatalks.club/tour"', body)

    def test_tour_opens_on_an_illustrated_hero_with_both_ways_in(self) -> None:
        """The hero is the course family landing's: words, then a drawing.

        The page uses a community scene rather than a course or solo-learner
        scene: the course cards further down each carry their family's scene,
        and the homepage is one link above this page.
        """

        body = self._get().content.decode()

        self.assertIn('class="tour-hero-inner"', body)
        self.assertIn('<h1>Take the tour</h1>', body)
        self.assertIn('class="tour-lede"', body)
        self.assertNotIn('class="tour-hero-commitment"', body)
        self.assertNotIn("Not a highlight reel", body)
        self.assertIn('class="cta cta-primary interactive-lift" href="/accounts/signup/"', body)
        self.assertIn('class="cta cta-secondary interactive-lift" href="/slack"', body)
        self.assertIn('class="tour-hero-art"', body)
        self.assertIn("tour-community.", body)
        self.assertIn("tour-community-dark.", body)
        self.assertNotIn("course-journey-start.", body)
        self.assertNotIn("home-hero.", body)

    def test_tour_opens_its_content_on_a_real_whos_here_section(self) -> None:
        """The numbers sit under a real band-head, like every other section.

        They used to be a bare stat strip with only an `aria-label`, followed
        by a small mono caption naming the survey -- the one section on the
        page with no visible heading.  The heading and its subline now open
        the section, matching the "Learn by building" band-head pattern, and
        the survey link sits under the tiles rather than in the head.
        """

        body = self._get().content.decode()

        self.assertIn('<h2 id="tour-stats-heading">Who\'s here</h2>', body)
        self.assertIn("From the community's own 2025 survey.", body)
        self.assertIn('<div class="stat-tiles tour-stat-tiles">', body)
        self.assertEqual(body.count('<div class="stat-tile">'), 4)
        self.assertIn("65+", body)
        self.assertIn("countries", body)
        self.assertIn(
            '<a class="band-link tour-content-link" '
            'href="/blog/datatalks-club-community-demographics.html">read the full survey →</a>',
            body,
        )
        self.assertLess(
            body.index('id="tour-stats-heading"'),
            body.index('class="stat-tiles tour-stat-tiles"'),
        )
        self.assertLess(
            body.index('class="stat-tiles tour-stat-tiles"'),
            body.index("read the full survey →"),
        )
        # No second ground: the mid-page ink stripe is gone.
        self.assertNotIn("tour-stats-grid", body)
        self.assertNotIn("--tour-stats-label", body)

    def test_tour_groups_its_sections_into_chapters_on_one_rule(self) -> None:
        """Every section is a chapter, at the same full breakout width.

        Eight sections at one weight, separated eight times by the same
        page-local `color-mix` rule, is what the page drew before: nothing
        grouped and nothing was a chapter.  A later pass grouped some of
        them into three chapters on the family landing's solid `var(--line)`
        rule with narrower subsections hanging under them -- which then read
        as an inconsistent width down the page, alternating wide and narrow.
        Every section is that same chapter now: one width, one rule, top to
        bottom.
        """

        source = (REPO_ROOT / "templates" / "core" / "tour.html").read_text(encoding="utf-8")
        body = self._get().content.decode()

        # Who's here, Learn by building, cohort week, more than courses,
        # Slack, how it started -- every section this fixture renders
        # (``build_reviewed_catalog`` carries no upcoming event, so the
        # events section itself does not render here).
        self.assertEqual(body.count("tour-chapter shell-breakout"), 6)
        self.assertNotIn("tour-subsection", body)
        self.assertNotIn("tour-section", body)
        self.assertNotIn("color-mix", source)
        self.assertIn("border-top: 2px solid var(--line);", source)

    def test_tour_draws_the_catalogue_teaser_as_illustrated_cards(self) -> None:
        """The teaser is the catalogue's own .catalog-card, one per family.

        ``build_reviewed_catalog`` seeds six course families -- the same six
        the full catalogue page draws.  The tour shows only the first three,
        each as a whole-card link with its family's own drawing, a mono cohort
        label and its homework/project line, and sends a reader who wants the
        rest to `/courses`.  The card is the shared design-system component
        `/courses` itself draws, not a bespoke tour card, so the two facts
        this page adds (the cohort tag, the homework/project line) sit inside
        the shared .catalog-card-body.
        """

        body = self._get().content.decode()

        self.assertEqual(
            body.count('class="card catalog-card interactive-card interactive-lift stretched-card-link"'),
            3,
        )
        self.assertEqual(body.count('class="catalog-card-media"'), 3)
        self.assertEqual(body.count('class="catalog-card-body"'), 3)
        self.assertIn('class="card-grid card-grid-3 tour-cards"', body)
        self.assertIn("see all 6 courses →", body)
        # The "see more" action sits under the cards, not beside the heading.
        self.assertIn('<a class="band-link tour-content-link" href="/courses"', body)
        self.assertLess(
            body.index('class="card-grid card-grid-3 tour-cards"'),
            body.index("see all 6 courses →"),
        )
        self.assertEqual(body.count('<a class="course-link" href="/courses/'), 3)
        self.assertIn('<span class="mono-label mono-label-indigo">2026 cohort</span>', body)
        self.assertIn("5 homework assignments · 2 projects", body)
        # A cohort the database gives no projects states only what it has.
        self.assertIn("4 homework assignments\n", body)
        self.assertNotIn("0 projects", body)
        # One drawing per family, and none of them the hero's.
        for slug in ("ai-dev-tools-zoomcamp", "de-zoomcamp", "llm-zoomcamp"):
            with self.subTest(family=slug):
                self.assertIn(f"course-{slug}.", body)
                self.assertIn(f"course-{slug}-dark.", body)
        self.assertNotIn("course-ml-zoomcamp.", body)
        self.assertNotIn('class="tour-aside"', body)

    def test_tour_states_its_real_scope_in_the_hero_lede(self) -> None:
        body = self._get().content.decode()

        self.assertIn("independent community for people who work with data", body)
        self.assertIn("free course", body)
        # The scope statement is the lede now, not a section with its own
        # heading competing with the page title.
        self.assertNotIn("What it actually is", body)

    def test_tour_prints_the_founder_story_with_a_real_attributed_link(self) -> None:
        """Two short real excerpts, on the homepage's member-story card.

        The story is the shared `.card` + `.story-quote` + `.story-person`
        component the homepage and a course family page draw for a member's
        quote -- not a tinted panel of its own -- and each excerpt is the
        strongest sentence or two of the transcript, not the whole passage.
        It closes the page's last chapter at the breakout width, where the two
        excerpts read as a spread rather than a stack.
        """

        body = self._get().content.decode()

        self.assertIn("How it started", body)
        self.assertIn('class="card tour-story"', body)
        self.assertNotIn('class="panel panel-mint', body)
        self.assertIn('<span class="mono-label">How it started</span>', body)
        self.assertIn('<span class="mono-label">Why it\'s free</span>', body)
        self.assertIn(
            "“I started DataTalks.Club four years ago by accident. … By September, "
            "restrictions were back, and we were stuck at home. That's when I "
            "thought, ‘Maybe I should start something.’”",
            body,
        )
        self.assertIn(
            "“I benefited a lot from free courses when I was starting my career in "
            "data science. So, this is my way of giving back to the community.”",
            body,
        )
        # The long middle of each passage stays in the transcript.
        self.assertNotIn("seaside in Germany", body)
        self.assertNotIn("What keeps me going", body)
        self.assertIn('class="story-person"', body)
        self.assertIn('href="https://www.youtube.com/watch?v=GHbeXIKnkLQ&amp;t=149s"', body)
        self.assertIn("Alexey Grigorev", body)
        self.assertIn(
            '<span class="story-context">Founder · DataTalks.Club Anniversary Podcast</span>',
            body,
        )
        # No founder record in the test catalogue: the shared stand-in disc.
        self.assertIn('<span class="avatar" aria-hidden="true"></span>', body)

    def test_tour_prints_the_cohort_week_facts_and_a_second_real_quote(self) -> None:
        """One page, one way of drawing a quote.

        The graduate's quote used to sit in a page-local italic well while the
        founder's took the shared member-story card, which is two components
        for one kind of content.  Both are the story card now.
        """

        body = self._get().content.decode()

        self.assertIn("What a cohort week looks like", body)
        self.assertIn('class="spec-strip"', body)
        self.assertIn("7–10 weeks", body)
        self.assertIn("10–15 hours", body)
        self.assertIn("no signup", body)
        self.assertIn("everything stays in a Jupyter notebook", body)
        self.assertIn('href="https://www.youtube.com/watch?v=B2tzuUg5uZs&amp;t=2190s"', body)
        self.assertIn("Dashel Ruiz Perez", body)
        self.assertIn('<span class="story-context">ML Zoomcamp graduate</span>', body)
        self.assertEqual(body.count('class="story-quote"'), 3)
        self.assertNotIn("tour-note-quote", body)

    def test_tour_prints_who_is_here_from_the_real_survey(self) -> None:
        body = self._get().content.decode()

        self.assertIn("2025 survey", body)
        self.assertIn("65+", body)
        self.assertIn("countries", body)
        self.assertIn('href="/blog/datatalks-club-community-demographics.html"', body)

    def test_tour_prints_real_slack_norms_as_numbered_steps(self) -> None:
        """Slack is both a card and its own section.

        It is also the hero's second action and the closing line, but the
        owner asked for a sixth card in the "More than courses" grid on top
        of that, so the card and the section below both name it -- Slack
        is not a countable catalogue item, so its card is always present
        like the YouTube channel's.  The norms take the homepage climb's
        numbered discs.
        """

        body = self._get().content.decode()

        self.assertIn('<a class="course-link" href="/slack">Slack</a>', body)
        self.assertIn("What Slack is actually like", body)
        self.assertIn('class="tour-norms"', body)
        self.assertEqual(body.count('class="tour-norm card"'), 3)
        self.assertIn('<span class="step-number" aria-hidden="true">1</span>', body)
        self.assertIn('<span class="step-number step-number-2" aria-hidden="true">2</span>', body)
        self.assertIn('<span class="step-number step-number-3" aria-hidden="true">3</span>', body)
        self.assertIn("Don't ask to ask", body)
        self.assertIn('href="https://dontasktoask.com/"', body)
        # The "join the Slack" action sits under the norms, not beside the
        # heading.
        self.assertIn('<a class="band-link tour-content-link" href="/slack">join the Slack →</a>', body)
        self.assertLess(
            body.index('class="tour-norms"'),
            body.index("join the Slack →"),
        )

    def test_tour_lists_every_other_channel_the_catalogue_holds(self) -> None:
        """One card per channel, each catalogue-backed card gated on its count.

        The YouTube channel and Slack are standing channels and always
        appear; the podcast, blog, books and wiki cards state their real
        counts in the mono foot; the docs card appears only when the
        documentation home is published.  They were six 80px rows of
        underlined title before, which is 712px of page for six links.
        """

        counts = {
            "articles": 7,
            "podcasts": 3,
            "books": 2,
            "people": 0,
            "wiki": 5,
            "courses": 0,
            "media": 0,
            "transcripts": 1,
        }
        with (
            mock.patch("core.views.catalogue.collection_counts", return_value=counts),
            mock.patch("core.views.docs_page", return_value={"title": "Docs"}),
        ):
            body = self._get().content.decode()

        self.assertIn("More than courses and events", body)
        # YouTube, Slack, events (no upcoming rows here), podcast, blog, books, wiki, docs.
        self.assertEqual(body.count('stretched-card-link tour-thing"'), 8)
        self.assertIn('href="https://www.youtube.com/c/DataTalksClub"', body)
        self.assertIn("YouTube channel", body)
        self.assertIn('<a class="course-link" href="/slack">Slack</a>', body)
        self.assertIn('<a class="course-link" href="/podcast">Podcast</a>', body)
        self.assertIn("3 conversations</span>", body)
        self.assertNotIn("with a full transcript", body)
        self.assertIn('<a class="course-link" href="/blog">Blog</a>', body)
        self.assertIn('<span class="mono-note">7 articles</span>', body)
        self.assertIn('<a class="course-link" href="/books">Book of the Week</a>', body)
        self.assertIn('<span class="mono-note">2 books</span>', body)
        self.assertIn('<a class="course-link" href="/wiki">Wiki</a>', body)
        self.assertIn("knowledge base", body)
        self.assertIn('<span class="mono-note">5 topics</span>', body)
        self.assertIn('<a class="course-link" href="/docs/">Docs</a>', body)

    def test_tour_names_events_in_the_channel_grid_only_without_a_teaser(
        self,
    ) -> None:
        """The events section renders from real rows or not at all.

        A development or freshly ingested database with no upcoming events
        gets no invented week: the section drops and the channel grid names
        the events page instead.
        """

        with mock.patch("core.views.event_groups", return_value=_event_groups()):
            with_teaser = self._get().content.decode()

        self.assertIn("tour-events-heading", with_teaser)
        self.assertNotIn('<a class="course-link" href="/events">Events</a>', with_teaser)
        # The events index's own date rail, from the record's own values.
        self.assertIn('<div class="when">', with_teaser)
        self.assertIn("<strong>Oct 1, 2026</strong>", with_teaser)
        self.assertIn("<span>20:00 CEST</span>", with_teaser)
        # The "see all events" action sits under the row list, not beside
        # the heading.
        self.assertIn('<a class="band-link tour-content-link" href="/events">see all events →</a>', with_teaser)
        self.assertLess(
            with_teaser.index('class="row-list"'),
            with_teaser.index("see all events →"),
        )

        groups = _event_groups()
        groups.upcoming = []
        with mock.patch("core.views.event_groups", return_value=groups):
            without_teaser = self._get().content.decode()

        self.assertNotIn("tour-events-heading", without_teaser)
        self.assertIn('<a class="course-link" href="/events">Events</a>', without_teaser)

    def test_tour_prints_upcoming_events(self) -> None:
        with mock.patch("core.views.event_groups", return_value=_event_groups()):
            body = self._get().content.decode()

        self.assertIn("Synthetic Office Hours", body)

    def test_tour_closes_on_the_ink_stripe_it_hides(self) -> None:
        """The page ends where every other page ends: on ink.

        The site-wide stripe is hidden here because it would link to this
        page, and what replaced it was two left-aligned buttons on lavender --
        the quietest ending on the site, on the page whose whole job is the
        ask.  The close is the stripe's own shape, drawn full-bleed inside the
        content band rather than as a second band.
        """

        body = self._get().content.decode()
        source = (REPO_ROOT / "templates" / "core" / "tour.html").read_text(encoding="utf-8")

        self.assertNotIn("tour-cta-heading", body)
        self.assertIn('class="tour-close shell-breakout"', body)
        self.assertIn('<h2 id="tour-community-heading">Build in public, together.</h2>', body)
        self.assertIn("background: var(--band-ink-bg);", source)
        self.assertNotIn("tour-actions", body)

    def test_tour_uses_the_shared_content_shell(self) -> None:
        source = (REPO_ROOT / "templates" / "core" / "tour.html").read_text(encoding="utf-8")

        self.assertIn('{% extends "core/content_page.html" %}', source)
        self.assertNotIn("<!DOCTYPE html>", source)
        self.assertNotIn('class="band band-cream', source)
        self.assertNotIn('class="band band-lavender', source)


class TourEmptyDatabaseTests(TestCase):
    """Sections with no rows drop instead of inventing copy.

    A database with reference events but no catalogue rows: the synced
    sources are disabled, so no courses and no podcast, wiki or docs
    catalogue rows exist, and those are the sections absent here. The
    survey numbers, the cohort-week facts and the origin story are not
    per-record content, so all three still render.
    """

    def setUp(self) -> None:
        super().setUp()
        # The premise is stated here rather than assumed of the surrounding
        # database: whichever reference rows a test database carries, this
        # class reads the tour exactly as an un-ingested database renders it.
        catalogue._records.cache_clear()
        catalogue._synced_records.cache_clear()
        catalogue._synced_courses.cache_clear()
        EngineContentSource.objects.update(is_enabled=False)

    def test_tour_without_courses_drops_the_courses_section(self) -> None:
        response = self.client.get(reverse("tour"))

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Build in public, together.", body)
        self.assertIn("independent community for people who work with data", body)
        self.assertIn("How it started", body)
        self.assertIn("What a cohort week looks like", body)
        self.assertIn('<div class="stat-tiles tour-stat-tiles">', body)
        self.assertIn("What Slack is actually like", body)
        self.assertNotIn("tour-courses-heading", body)
        self.assertNotIn("AI Dev Tools Zoomcamp", body)

    def test_tour_without_catalogue_rows_keeps_only_the_standing_channels(
        self,
    ) -> None:
        body = self.client.get(reverse("tour")).content.decode()

        self.assertIn("tour-more-heading", body)
        self.assertIn('href="https://www.youtube.com/c/DataTalksClub"', body)
        self.assertIn('<a class="course-link" href="/slack">Slack</a>', body)
        for path in ("/podcast", "/blog", "/books", "/wiki", "/docs/"):
            with self.subTest(path=path):
                self.assertNotIn(f'<a class="course-link" href="{path}">', body)


class TourStripeTests(TestCase):
    def test_stripe_shows_on_shared_shell_pages(self) -> None:
        for name in ("sponsors",):
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)
                body = response.content.decode()
                self.assertIn("tour-cta-heading", body)
                self.assertIn('href="/tour"', body)

    def test_stripe_stays_off_the_homepage(self) -> None:
        build_reviewed_catalog()

        body = self.client.get(reverse("home")).content.decode()

        self.assertNotIn("tour-cta-heading", body)
        # The homepage keeps its own closing band instead.
        self.assertIn('id="closing-heading"', body)
