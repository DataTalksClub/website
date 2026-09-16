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
        body = self._get().content.decode()

        self.assertIn("How it started", body)
        self.assertIn("DataTalks.Club four years ago by accident", body)
        self.assertIn(
            "I benefited a lot from free courses when I was starting my career",
            body,
        )
        self.assertIn(
            'href="https://www.youtube.com/watch?v=GHbeXIKnkLQ&amp;t=149s"', body
        )
        self.assertIn("DataTalks.Club Anniversary Podcast", body)

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

    def test_tour_points_at_the_podcast_and_wiki_when_the_catalogue_holds_them(
        self,
    ) -> None:
        counts = {
            "articles": 0,
            "podcasts": 3,
            "books": 0,
            "people": 0,
            "wiki": 5,
            "courses": 0,
            "media": 0,
            "transcripts": 0,
        }
        with mock.patch("core.views.catalogue.collection_counts", return_value=counts):
            body = self._get().content.decode()

        self.assertIn("More than courses and events", body)
        self.assertIn('href="/podcast"', body)
        self.assertIn('href="/wiki"', body)

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

    def test_tour_without_podcast_or_wiki_rows_drops_the_more_section(self) -> None:
        body = self.client.get(reverse("tour")).content.decode()

        self.assertNotIn("tour-more-heading", body)


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
