"""Published tour copy and stories have no file or hardcoded-content fallback."""

import json
from itertools import product
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse

from content.models import ContentDocument, ContentSource
from content.tour_content import TOUR_SOURCE_ID, tour_page
from courses.models import Course, Testimonial
from courses.services.testimonials import import_homepage_testimonials, tour_testimonials
from scripts.prod.import_tour import run
from test_support.course_catalog import build_reviewed_catalog


def synthetic_page():
    """Test-only editorial input; no production quotation is copied here."""
    return {
        "hero_intro": "A synthetic community for learning together.",
        "stats_intro": "These figures describe synthetic survey respondents.",
        "stats": [{"value": str(i), "label": f"Survey group {i}"} for i in range(4)],
        "courses_intro": "Recorded lessons and projects in a synthetic course.",
        "week_intro": "Join a cohort or read materials independently.",
        "week": [
            {"title": "Learn", "body": "Recorded lessons and optional homework."},
            {"title": "Ask", "body": "Join optional live sessions or watch recordings."},
            {"title": "Build", "body": "Projects and peer reviews depend on the course."},
        ],
        "week_note": "Check the course requirements; self-paced study has no certificate.",
        "community_intro": "A synthetic community overview.",
        "community": [
            {"title": title, "body": f"Synthetic description for {title.lower()}."}
            for title in ("Learn together", "Meet practitioners", "Share what you build")
        ],
        "founder_intro": "In their own words, from a synthetic anniversary conversation.",
        "founder_context": "A synthetic founder started an online community during restrictions.",
        "founder_quote": "A complete synthetic quotation about sharing free education.",
        "founder_name": "Synthetic Founder",
        "founder_attribution": "Founder · Synthetic anniversary episode",
        "founder_source_url": "https://example.com/founder-source",
        "testimonials_intro": "Three synthetic member experiences.",
        "more_intro": "Explore the community resources.",
        "close_intro": "Take part with a free account.",
        "channels": {
            key: f"Synthetic {key} description."
            for key in (
                "youtube",
                "slack",
                "events",
                "podcasts",
                "articles",
                "books",
                "wiki",
                "docs",
            )
        },
        "sources": ["https://example.com/editorial-source"],
    }


class TourPageTests(TestCase):
    def setUp(self):
        build_reviewed_catalog()
        scratch = Path(".tmp/test-tour")
        scratch.mkdir(parents=True, exist_ok=True)
        self.temporary = TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "tour.json"
        self.page = synthetic_page()
        self.path.write_text(json.dumps({"schema_version": 1, "page": self.page}))
        run(path=self.path)
        for index, name in enumerate(
            ("First Learner", "Second Learner", "A Long Public Member Byline")
        ):
            Testimonial.objects.create(
                placement="tour",
                name=name,
                attribution="Synthetic course participant",
                quote=f"Synthetic experience {index}.",
                source_url=f"https://example.com/member-{index}",
                position=index,
                published=True,
            )

    def body(self):
        response = self.client.get(reverse("tour"))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_database_copy_catalogue_and_three_stories_render(self):
        body = self.body()
        self.assertIn(self.page["hero_intro"], body)
        self.assertIn("AI Dev Tools Zoomcamp", body)
        self.assertIn("see all 6 courses", body)
        self.assertEqual(body.count('class="catalog-card-media"'), 3)
        self.assertEqual(body.count('href="https://example.com/member-'), 3)
        self.assertIn("A Long Public Member Byline", body)
        self.assertIn('rel="canonical" href="https://datatalks.club/tour"', body)

    def test_copy_remains_after_staging_is_removed(self):
        self.path.unlink()
        with mock.patch("pathlib.Path.read_text", side_effect=AssertionError("file read")):
            self.assertEqual(tour_page(), self.page)
        self.assertIn(self.page["founder_quote"], self.body())

    def test_facets_are_unordered_and_keep_slack_action(self):
        body = self.body()
        self.assertIn("What the community is like", body)
        self.assertIn('<ul class="tour-norms">', body)
        for title in ("Learn together", "Meet practitioners", "Share what you build"):
            self.assertIn(title, body)
        self.assertNotIn("dontasktoask", body)
        self.assertNotIn('class="step-number', body)
        self.assertIn('href="/slack">join the Slack →</a>', body)

    def test_learning_has_no_universal_requirements(self):
        body = self.body()
        self.assertIn("How learning together works", body)
        self.assertIn("optional homework", body)
        self.assertIn("self-paced study has no certificate", body)
        for claim in (
            "7–10 weeks",
            "10–15 hours",
            "2–3 weeks",
            "Free, incl. certificate",
            "no signup",
            "same shape for every zoomcamp",
        ):
            self.assertNotIn(claim, body)

    def test_founder_has_context_neutral_caption_and_source(self):
        body = self.body()
        self.assertIn(self.page["founder_context"], body)
        self.assertIn(f"“{self.page['founder_quote']}”", body)
        self.assertIn("In their own words,", body)
        self.assertIn('href="https://example.com/founder-source"', body)
        self.assertNotIn("In his own words", body)
        self.assertNotIn("by accident. …", body)

    def test_missing_portrait_keeps_story_and_decorative_avatar(self):
        with mock.patch.object(
            Testimonial, "portrait_url", new_callable=mock.PropertyMock, return_value=""
        ):
            body = self.body()
        self.assertIn("First Learner", body)
        self.assertIn('class="avatar tour-portrait" aria-hidden="true"', body)
        self.assertNotIn('src=""', body)
        self.assertIn("if (image.complete && image.naturalWidth === 0) useAvatar();", body)

    def test_survey_describes_respondents_and_preserves_source(self):
        body = self.body()
        self.assertIn("survey respondents", body)
        self.assertIn('href="/blog/datatalks-club-community-demographics.html"', body)

    def test_hero_is_grammatical_for_every_count_combination(self):
        for courses, events, podcasts, wiki in product((0, 1), repeat=4):
            with self.subTest(courses=courses, events=events, podcasts=podcasts, wiki=wiki):
                body = render_to_string(
                    "core/tour.html",
                    {
                        "tour_content": self.page,
                        "course_family_count": courses,
                        "counts": {"podcasts": podcasts, "wiki": wiki},
                    },
                )
                self.assertIn(self.page["hero_intro"], body)
                self.assertNotIn("It comes down to", body)
                self.assertNotIn(", and a Slack", body)
                self.assertEqual("Explore <strong>1 free course" in body, bool(courses))
                self.assertEqual("Listen to <strong>1 podcast conversation" in body, bool(podcasts))

    def test_missing_editorial_or_stories_has_no_fallback(self):
        ContentSource.objects.filter(stable_id=TOUR_SOURCE_ID).update(enabled=False)
        Testimonial.objects.filter(placement="tour").delete()
        body = self.body()
        for heading in ("tour-stories-heading", "tour-week-heading", "tour-story-heading"):
            self.assertNotIn(heading, body)
        self.assertNotIn("Synthetic Founder", body)
        self.assertIn('href="/slack"', body)
        self.assertIn("AI Dev Tools Zoomcamp", body)

    def test_query_scopes_orders_limits_and_requires_attribution(self):
        for placement, published, name in (
            ("tour", False, "Hidden"),
            ("homepage", True, "Homepage only"),
            ("course", True, "Course only"),
        ):
            Testimonial.objects.create(
                placement=placement,
                published=published,
                name=name,
                quote="Other quote",
                course=Course.objects.first() if placement == "course" else None,
                source_url="https://example.com/other",
                position=0,
            )
        Testimonial.objects.create(
            placement="tour", name="No source", quote="Unattributed", published=True
        )
        Testimonial.objects.create(
            placement="tour",
            name="Fourth",
            quote="Extra",
            source_url="https://example.com/extra",
            published=True,
            position=10,
        )
        with self.assertNumQueries(1):
            names = [story.name for story in tour_testimonials()]
        self.assertEqual(names, ["First Learner", "Second Learner", "A Long Public Member Byline"])
        Testimonial.objects.filter(name="A Long Public Member Byline").update(position=0)
        self.assertEqual(tour_testimonials()[1].name, "A Long Public Member Byline")

    def test_editorial_import_validates_before_mutation_and_replays(self):
        self.assertTrue(run(path=self.path)["replayed"])
        self.assertEqual(ContentDocument.objects.filter(content_kind="tour_page").count(), 1)
        invalid = {
            "schema_version": 1,
            "page": {**self.page, "founder_source_url": "javascript:alert(1)"},
        }
        self.path.write_text(json.dumps(invalid))
        with self.assertRaisesRegex(ValueError, "reviewed_tour_invalid"):
            run(path=self.path)
        self.assertEqual(ContentDocument.objects.filter(content_kind="tour_page").count(), 1)

    def test_tour_testimonial_import_is_separate_and_replay_safe(self):
        path = Path(self.temporary.name) / "testimonials.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "testimonials": [
                        {
                            "placement": "tour",
                            "name": "Imported Learner",
                            "attribution": "Participant",
                            "quote": "A synthetic imported story.",
                            "source_url": "https://example.com/imported",
                            "portrait_asset_key": "",
                        }
                    ],
                }
            )
        )
        self.assertEqual(import_homepage_testimonials(path).created, 1)
        self.assertTrue(import_homepage_testimonials(path).replayed)
        self.assertEqual(
            Testimonial.objects.get(source_url="https://example.com/imported").placement, "tour"
        )


class TourStripeTests(TestCase):
    def test_tour_does_not_advertise_itself(self):
        body = self.client.get(reverse("tour")).content.decode()
        self.assertNotIn("tour-cta-heading", body)
        self.assertIn("tour-community-heading", body)

    def test_other_public_pages_keep_tour_destination(self):
        for route in ("course_list", "events"):
            with self.subTest(route=route):
                body = self.client.get(reverse(route)).content.decode()
                self.assertIn('href="/tour"', body)
