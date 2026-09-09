"""The community tour page and the site-wide tour stripe.

``/tour`` composes sections the site already publishes elsewhere -- the course
catalogue, upcoming events, member stories, sponsors -- so it can only promise
what the database holds. The black stripe above the footer advertises the tour
on every shared-shell page except the homepage (which ends with its own
closing band) and the tour itself.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from courses.models.testimonial import Testimonial, TestimonialPlacement
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
    upcoming, recent = list(UPCOMING), []

    class Groups:
        pass

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

    def test_tour_prints_member_stories(self) -> None:
        Testimonial.objects.create(
            placement=TestimonialPlacement.HOMEPAGE,
            quote="The tour brought me here.",
            name="Tour Reader",
            attribution="Role · City",
            position=0,
            published=True,
        )

        body = self._get().content.decode()

        self.assertIn("The tour brought me here.", body)
        self.assertIn("Tour Reader", body)

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
        self.assertIn("Northwind Analytics", body)

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

    Every Django test database carries reviewed reference events and
    testimonials, but no courses and no sponsors, so the courses and sponsors
    sections are the ones absent here. Deleting the testimonials proves the
    stories section drops the same way.
    """

    def test_tour_without_courses_or_sponsors_drops_those_sections(self) -> None:
        response = self.client.get(reverse("tour"))

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Build in public, together", body)
        self.assertNotIn("tour-courses-heading", body)
        self.assertNotIn("tour-sponsors-heading", body)
        self.assertNotIn("AI Dev Tools Zoomcamp", body)

    def test_tour_without_stories_drops_the_stories_section(self) -> None:
        Testimonial.objects.all().delete()

        body = self.client.get(reverse("tour")).content.decode()

        self.assertNotIn("tour-stories-heading", body)


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
