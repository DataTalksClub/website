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
renders. The black stripe above the footer advertises the tour on every
shared-shell page except the homepage (which ends with its own closing
band) and the tour itself.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from test_support.course_catalog import build_reviewed_catalog

REPO_ROOT = Path(__file__).resolve().parents[2]
UPCOMING = (
    {
        "title": "Synthetic Office Hours",
        "public_path": "/events/synthetic-office-hours",
        "starts_at": "2026-10-01T18:00:00+00:00",
        "home_time": "Oct 1, 2026",
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

    def test_tour_teases_the_catalogue_instead_of_redrawing_it(self) -> None:
        """The tour points at the catalogue page rather than re-listing it.

        ``build_reviewed_catalog`` seeds six course families -- the same six
        the full catalogue page draws as its own card grid. The tour shows
        only a handful, in the light dashed row-list shared with index pages
        like the wiki hub, not the catalogue's bordered card grid, and sends
        a reader who wants the rest to `/courses`.
        """

        body = self._get().content.decode()

        self.assertIn('class="row-list"', body)
        self.assertNotIn("tour-grid-3", body)
        self.assertIn("see all 6 courses →", body)
        # Six families exist; the teaser shows only the first three.
        self.assertEqual(body.count('<h3><a href="/courses/'), 3)

    def test_tour_states_its_real_scope_from_the_catalogue_counts(self) -> None:
        body = self._get().content.decode()

        self.assertIn("What it actually is", body)
        self.assertIn("free course", body)

    def test_tour_prints_the_founder_story_with_a_real_attributed_link(self) -> None:
        """Two short real excerpts, on the homepage's member-story card.

        The story is the shared `.card` + `.story-quote` + `.story-person`
        component the homepage and a course family page draw for a member's
        quote -- not a tinted panel of its own -- and each excerpt is the
        strongest sentence or two of the transcript, not the whole passage.
        """

        body = self._get().content.decode()

        self.assertIn("How it started", body)
        self.assertIn('class="card tour-story"', body)
        self.assertNotIn('class="panel panel-mint', body)
        self.assertIn('<span class="mono-label">How it started</span>', body)
        self.assertIn('<span class="mono-label">Why it\'s free</span>', body)
        self.assertEqual(body.count('class="story-quote"'), 2)
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
        self.assertIn(
            'href="https://www.youtube.com/watch?v=GHbeXIKnkLQ&amp;t=149s"', body
        )
        self.assertIn("Alexey Grigorev", body)
        self.assertIn(
            '<span class="story-context">Founder · DataTalks.Club Anniversary Podcast</span>',
            body,
        )
        # No founder record in the test catalogue: the shared stand-in disc.
        self.assertIn('<span class="avatar" aria-hidden="true"></span>', body)

    def test_tour_prints_the_cohort_week_facts_and_a_second_real_quote(self) -> None:
        body = self._get().content.decode()

        self.assertIn("What a cohort week looks like", body)
        self.assertIn("7–10 weeks", body)
        self.assertIn("10–15 hours", body)
        self.assertIn("no signup", body)
        self.assertIn("everything stays in a Jupyter notebook", body)
        self.assertIn(
            'href="https://www.youtube.com/watch?v=B2tzuUg5uZs&amp;t=2190s"', body
        )
        self.assertIn("Dashel Ruiz Perez", body)

    def test_tour_prints_who_is_here_from_the_real_survey(self) -> None:
        body = self._get().content.decode()

        self.assertIn("Who's here", body)
        self.assertIn("65+", body)
        self.assertIn("countries", body)
        self.assertIn(
            'href="/blog/datatalks-club-community-demographics.html"', body
        )

    def test_tour_prints_real_slack_norms(self) -> None:
        body = self._get().content.decode()

        self.assertIn("What Slack is actually like", body)
        self.assertIn("Don't ask to ask", body)
        self.assertIn('href="https://dontasktoask.com/"', body)

    def test_tour_lists_every_other_channel_the_catalogue_holds(self) -> None:
        """One row per channel, each catalogue-backed row gated on its count.

        The Slack and the YouTube channel are standing channels and always
        appear; the podcast, blog, books and wiki rows state their real
        counts; the docs row appears only when the documentation home is
        published.
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
        self.assertIn('<h3><a href="/slack">Slack</a></h3>', body)
        self.assertIn('href="https://www.youtube.com/c/DataTalksClub"', body)
        self.assertIn("YouTube channel", body)
        self.assertIn('<h3><a href="/podcast">Podcast</a></h3>', body)
        self.assertIn("3 conversations with practitioners, 1 with a full transcript.", body)
        self.assertIn('<h3><a href="/blog">Blog</a></h3>', body)
        self.assertIn("7 articles written by members and guests.", body)
        self.assertIn('<h3><a href="/books">Book of the Week</a></h3>', body)
        self.assertIn("2 books whose authors", body)
        self.assertIn('<h3><a href="/wiki">Wiki</a></h3>', body)
        self.assertIn("5 member-written topics, A–Z.", body)
        self.assertIn('<h3><a href="/docs/">Docs</a></h3>', body)

    def test_tour_names_events_in_the_channel_list_only_without_a_teaser(
        self,
    ) -> None:
        with mock.patch("core.views.event_groups", return_value=_event_groups()):
            with_teaser = self._get().content.decode()

        self.assertIn("tour-events-heading", with_teaser)
        self.assertNotIn('<h3><a href="/events">Events</a></h3>', with_teaser)

        groups = _event_groups()
        groups.upcoming = []
        with mock.patch("core.views.event_groups", return_value=groups):
            without_teaser = self._get().content.decode()

        self.assertNotIn("tour-events-heading", without_teaser)
        self.assertIn('<h3><a href="/events">Events</a></h3>', without_teaser)

    def test_tour_prints_upcoming_events_and_sponsors(self) -> None:
        sponsors = (
            {
                "name": "Northwind Analytics",
                "url": "https://northwind.example.invalid",
                "description": "A synthetic featured sponsor.",
                "logo_url": "/images/synthetic.png",
            },
        )
        with (
            mock.patch("core.views.event_groups", return_value=_event_groups()),
            mock.patch("core.views.public_sponsors", return_value=sponsors),
        ):
            body = self._get().content.decode()

        self.assertIn("Synthetic Office Hours", body)
        self.assertIn("Kept free by sponsors", body)
        self.assertIn('href="/sponsors"', body)

    def test_tour_hides_its_own_stripe(self) -> None:
        body = self._get().content.decode()

        self.assertNotIn("tour-cta-heading", body)

    def test_tour_uses_the_shared_content_shell(self) -> None:
        source = (REPO_ROOT / "templates" / "core" / "tour.html").read_text(encoding="utf-8")

        self.assertIn('{% extends "core/content_page.html" %}', source)
        self.assertNotIn("<!DOCTYPE html>", source)
        self.assertNotIn('class="band band-cream', source)
        self.assertNotIn('class="band band-lavender', source)


class TourEmptyDatabaseTests(TestCase):
    """Sections with no rows drop instead of inventing copy.

    Every Django test database carries reviewed reference events, but no
    courses, no sponsors, and no podcast or wiki catalogue rows, so those
    are the sections absent here. The scope statement and the origin story
    are not per-record content, so both still render.
    """

    def test_tour_without_courses_or_sponsors_drops_those_sections(self) -> None:
        response = self.client.get(reverse("tour"))

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Build in public, together", body)
        self.assertIn("What it actually is", body)
        self.assertIn("How it started", body)
        self.assertIn("What a cohort week looks like", body)
        self.assertIn("Who's here", body)
        self.assertIn("What Slack is actually like", body)
        self.assertNotIn("tour-courses-heading", body)
        self.assertNotIn("Kept free by sponsors", body)
        self.assertNotIn("AI Dev Tools Zoomcamp", body)

    def test_tour_without_catalogue_rows_keeps_only_the_standing_channels(
        self,
    ) -> None:
        body = self.client.get(reverse("tour")).content.decode()

        self.assertIn("tour-more-heading", body)
        self.assertIn('<h3><a href="/slack">Slack</a></h3>', body)
        self.assertIn('href="https://www.youtube.com/c/DataTalksClub"', body)
        for path in ("/podcast", "/blog", "/books", "/wiki", "/docs/"):
            with self.subTest(path=path):
                self.assertNotIn(f'<h3><a href="{path}">', body)


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
