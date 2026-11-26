"""The About page: published copy and stories have no file or hardcoded-content fallback.

The page was published as the tour, which is the name its view, template,
editorial record and tests still carry.
"""

import json
import re
from itertools import product
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from django.db import OperationalError
from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse

from content.models import ContentDocument, ContentSource
from content.tour_content import TOUR_SOURCE_ID, homepage_tour_page, tour_page
from core import views as core_views
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


class PublishedTourPage(TestCase):
    """The About page's record and stories published, for every page that reads them."""

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


class TourPageTests(PublishedTourPage):
    def body(self):
        response = self.client.get(reverse("about"))
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
        self.assertIn('rel="canonical" href="https://datatalks.club/about"', body)

    def test_the_page_introduces_itself_as_the_about_page(self):
        body = self.body()
        self.assertRegex(body, r"<title>About DataTalks\.Club</title>")
        self.assertIn("<h1>About DataTalks.Club</h1>", body)
        self.assertNotIn("Take the tour", body)

    def test_survey_figures_and_founder_story_are_the_shared_includes(self):
        body = self.body()
        self.assertIn('class="stat-tiles stat-tiles-row community-stats"', body)
        self.assertIn('class="card founder-story"', body)

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

    def test_the_founder_quote_leads_with_its_question_and_links_its_episode(self):
        page = {
            **self.page,
            "founder_question": "Why did you choose to create a free-to-learn community?",
            "founder_source_url": "/podcast/s19e03/synthetic-anniversary",
        }
        self.path.write_text(json.dumps({"schema_version": 1, "page": page}))
        self.assertTrue(run(path=self.path)["applied"])
        for url in ("/", "/about"):
            with self.subTest(url=url):
                body = self.client.get(url).content.decode()
                found = re.search(r'<figure class="card founder-story">.*?</figure>', body, re.S)
                assert found is not None
                card = found.group()
                question = card.index('<p class="story-question">Why did you choose to create')
                self.assertLess(question, card.index(page["founder_quote"]))
                self.assertIn(
                    '<a class="person-chip-role" href="/podcast/s19e03/synthetic-anniversary">'
                    f"{page['founder_attribution']}</a>",
                    card,
                )
                self.assertNotIn("target=", card[card.index("person-chip-role") :])
        page["founder_source_url"] = "//evil.example/x"
        self.path.write_text(json.dumps({"schema_version": 1, "page": page}))
        with self.assertRaisesRegex(ValueError, "reviewed_tour_invalid"):
            run(path=self.path)

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
    def test_the_about_page_does_not_advertise_itself(self):
        body = self.client.get(reverse("about")).content.decode()
        self.assertNotIn("tour-cta-heading", body)
        self.assertIn("tour-community-heading", body)

    def test_newcomer_pages_lead_to_the_about_page(self):
        # The stripe is selective (#546): it renders on newcomer-facing pages only.
        for route in ("events", "faq-home"):
            with self.subTest(route=route):
                body = self.client.get(reverse(route)).content.decode()
                self.assertIn("tour-cta-heading", body)
                self.assertIn('href="/about">About DataTalks.Club</a>', body)
                self.assertNotIn('href="/tour"', body)

    def test_slack_landing_page_ends_with_its_own_closing_panel(self):
        # /slack is a landing page with one goal (Join Slack); it closes on its
        # own panel, so the About stripe is not stacked under it.
        body = self.client.get(reverse("slack")).content.decode()
        self.assertNotIn("tour-cta-heading", body)

    def test_courses_shows_academy_stripe_without_about_stripe(self):
        body = self.client.get(reverse("course_list")).content.decode()
        self.assertIn("data-academy-cta", body)
        self.assertNotIn("tour-cta-heading", body)
        self.assertNotIn('href="/about">About DataTalks.Club</a>', body)


class AboutAddressTests(TestCase):
    """/about is the page's address; the old tour spellings are gone."""

    def test_about_is_served_at_its_own_address(self):
        response = self.client.get("/about")
        self.assertEqual(response.status_code, 200)
        self.assertIs(response.resolver_match.func, core_views.tour)

    def test_the_trailing_slash_spelling_redirects_once_to_about(self):
        response = self.client.get("/about/?source=browser", follow=False)
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.headers["Location"], "/about?source=browser")

    def test_the_old_tour_addresses_are_not_served(self):
        for source in ("/tour", "/tour/"):
            with self.subTest(source=source):
                self.assertEqual(self.client.get(source).status_code, 404)


class HomepageAboutBandTests(PublishedTourPage):
    """The homepage answers "what is this community" with the About page's own blocks.

    The band is a second reader of the same published record and holds no copy of
    its own: nothing published means no band.  The homepage is also the page the
    container liveness probe requests, so a record that cannot be read costs it
    the band, never the page.
    """

    def body(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_homepage_reuses_the_about_page_intro_figures_and_founder_story(self):
        body = self.body()
        self.assertIn('<h2 id="about-heading">What is DataTalks.Club?</h2>', body)
        self.assertIn(self.page["hero_intro"], body)
        # The About page's own shared includes, not a homepage redraw: the
        # figures as the base 2x2 tiles, the founder card quote-only (the
        # band tells how it started in its own prose, without a mono label).
        self.assertIn('class="stat-tiles community-stats"', body)
        self.assertIn('class="card founder-story"', body)
        self.assertIn(f'<p class="about-origin">{self.page["founder_context"]}</p>', body)
        card = re.search(r'<figure class="card founder-story">.*?</figure>', body, re.DOTALL)
        assert card is not None
        self.assertIn(self.page["founder_quote"], card.group())
        self.assertNotIn(self.page["founder_context"], card.group())
        self.assertNotIn("mono-label", card.group())
        for stat in self.page["stats"]:
            self.assertIn(f"<strong>{stat['value']}</strong>", body)
            self.assertIn(stat["label"], body)
        self.assertIn(
            f'<a class="band-link" href="{reverse("about")}">about DataTalks.Club →</a>', body
        )
        self.assertIn('href="/blog/datatalks-club-community-demographics.html"', body)

    def test_without_a_published_record_the_homepage_has_no_band(self):
        ContentSource.objects.filter(stable_id=TOUR_SOURCE_ID).update(enabled=False)
        body = self.body()
        # The band's section markup, not the bare class: ``home-about`` also
        # appears as a selector in the page's <style>.
        self.assertNotIn('aria-labelledby="about-heading"', body)
        self.assertNotIn("What is DataTalks.Club?", body)
        self.assertNotIn(self.page["hero_intro"], body)
        self.assertNotIn("Synthetic Founder", body)
        self.assertNotIn('<figure class="card founder-story">', body)
        self.assertNotIn("community-stats", body.split("</style>")[-1])

    def test_the_band_teases_the_about_page_and_does_not_replace_it(self):
        body = self.body()
        for key in ("courses_intro", "week_intro", "community_intro", "more_intro"):
            self.assertNotIn(self.page[key], body)

    def test_an_unreadable_record_costs_the_homepage_the_band_and_not_the_page(self):
        # The homepage view holds no guard of its own: the reader it calls is
        # what keeps an outage from reaching the page.
        with (
            mock.patch(
                "content.tour_content.tour_page",
                side_effect=OperationalError("database unavailable"),
            ) as strict_read,
            mock.patch.object(core_views, "_founder_chip") as founder_chip,
        ):
            response = self.client.get(reverse("home"), follow=False)
        self.assertEqual(response.status_code, 200)
        strict_read.assert_called_once_with()
        founder_chip.assert_not_called()
        body = response.content.decode()
        self.assertIn("Learn the fundamentals. Build real projects. Share your work.", body)
        # The band's section markup, not the bare class: ``home-about`` also
        # appears as a selector in the page's <style>.
        self.assertNotIn('aria-labelledby="about-heading"', body)
        self.assertNotIn(self.page["hero_intro"], body)

    def test_the_homepage_read_answers_an_outage_with_no_record(self):
        """``homepage_tour_page`` is ``tour_page`` plus the outage guard, nothing else."""

        self.assertEqual(homepage_tour_page(), tour_page())
        self.assertEqual(homepage_tour_page(), self.page)
        with mock.patch(
            "content.tour_content.tour_page", side_effect=OperationalError("database unavailable")
        ):
            self.assertEqual(homepage_tour_page(), {})

    def test_an_unreadable_portrait_costs_the_band_only_its_portrait(self):
        with mock.patch.object(
            core_views, "_founder_chip", side_effect=OperationalError("database unavailable")
        ):
            body = self.body()
        card = re.search(r'<figure class="card founder-story">.*?</figure>', body, re.DOTALL)
        assert card is not None, "the founder story is still published"
        self.assertIn(self.page["founder_quote"], card.group())
        self.assertIn(
            f'<span class="person-chip-name">{self.page["founder_name"]}</span>', card.group()
        )
        self.assertNotIn("<img", card.group())
