from datetime import timedelta
from html.parser import HTMLParser
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.staticfiles import finders
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from courses.models import Cohort, Course, RegistrationCampaign
from courses.views.course_list import hero_collage_cards

COURSE_SLUGS = (
    "ai-dev-tools-zoomcamp",
    "de-zoomcamp",
    "ml-zoomcamp",
    "mlops-zoomcamp",
    "llm-zoomcamp",
    "sma-zoomcamp",
)


class ImageParser(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.images = []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        if tag == "img":
            self.images.append(dict(attrs))


class CourseIllustrationTests(SimpleTestCase):
    def render_images(self, **context):
        return ImageParser(render_to_string("core/_course_illustration.html", context)).images

    def test_each_known_family_uses_its_installed_artwork(self):
        for slug in COURSE_SLUGS:
            with self.subTest(slug=slug):
                images = self.render_images(family_slug=slug)
                self.assertEqual(len(images), 2)
                light, dark = images
                asset = f"core/illustrations/course-{slug}.webp"
                self.assertEqual(light["src"], static(asset))
                self.assertIsNotNone(finders.find(asset))
                self.assertEqual((light["width"], light["height"]), ("1254", "1254"))
                self.assertEqual(
                    dark["src"],
                    static(f"core/illustrations/course-{slug}-dark.webp"),
                )
                self.assertIsNotNone(finders.find(f"core/illustrations/course-{slug}-dark.webp"))
                self.assertEqual((dark["width"], dark["height"]), ("1254", "1254"))
                for image in images:
                    self.assertEqual(image["alt"], "")
                    self.assertEqual(image["decoding"], "async")
                    self.assertEqual(image["loading"], "eager")

    def test_unknown_and_missing_family_keep_generic_pair(self):
        for context in ({}, {"family_slug": "unrecognized-course"}):
            with self.subTest(context=context):
                light, dark = self.render_images(**context)
                self.assertEqual(light["src"], static("core/illustrations/course-learning.webp"))
                self.assertEqual(
                    dark["src"], static("core/illustrations/course-learning-dark.webp")
                )
                self.assertEqual((light["width"], light["height"]), ("1024", "1024"))

    def test_below_fold_card_images_can_load_lazily(self):
        for image in self.render_images(family_slug="ml-zoomcamp", loading="lazy"):
            self.assertEqual(image["loading"], "lazy")

    def test_collage_keeps_database_title_with_family_after_ordering_and_deduplication(self):
        def card(slug, title):
            return SimpleNamespace(family=SimpleNamespace(slug=slug), title=title)

        active = [
            card("de-zoomcamp", "Database DE title"),
            card("ml-zoomcamp", "Database ML title"),
            card("new-course", "New course title"),
        ]
        upcoming = [
            card("ml-zoomcamp", "Duplicate ML title"),
            card("another-course", "Another title"),
            card("fifth-course", "Fifth title"),
        ]
        with patch("courses.views.course_list.COURSE_FAMILIES", [("ml-zoomcamp", "Not copy")]):
            cards = hero_collage_cards(active, upcoming)
        self.assertEqual(
            cards,
            [
                {"family_slug": "ml-zoomcamp", "title": "Database ML title"},
                {"family_slug": "de-zoomcamp", "title": "Database DE title"},
                {"family_slug": "new-course", "title": "New course title"},
                {"family_slug": "another-course", "title": "Another title"},
            ],
        )

    def test_empty_catalogue_has_no_invented_collage(self):
        self.assertEqual(hero_collage_cards([], []), [])


class CourseIllustrationPageTests(TestCase):
    def test_existing_card_sections_keep_actions_and_matching_art(self):
        # One consistent card for every state now (no badge, no per-state
        # background or media class) -- the owner's "no special treatment"
        # ask -- so every family renders the same ``catalog-card-media`` class
        # and links to its family page, whatever its real registration state.
        states = ("open_registration", "active", "finished")
        for slug, state in zip(("ml-zoomcamp", "de-zoomcamp", "llm-zoomcamp"), states, strict=True):
            family, _ = Course.objects.get_or_create(slug=slug, defaults={"title": slug})
            cohort = Cohort.objects.create(
                course=family,
                slug=f"card-{slug}",
                identifier="card-test",
                title=family.title,
                finished=state == "finished",
                start_date=timezone.localdate() + timedelta(days=30),
                registration_url="https://example.org/register"
                if state == "open_registration"
                else "",
            )
            if state == "open_registration":
                RegistrationCampaign.objects.create(
                    slug="art-open-registration",
                    title=family.title,
                    current_course=cohort,
                )
            with self.subTest(state=state):
                response = self.client.get(reverse("course_list"))
                self.assertContains(response, 'class="catalog-card-media"')
                self.assertContains(response, static(f"core/illustrations/course-{slug}.webp"))
                self.assertContains(response, reverse("course_family", args=[slug]))
                self.assertNotContains(response, "mascot needed")

    def test_catalogue_ignores_campaign_art_for_one_consistent_icon(self):
        """Every catalogue card draws the same generic icon, campaign art or not.

        A campaign's own hero photo is not guaranteed to be square (several are
        wide banners with baked-in text), so drawing it in the catalogue's small,
        uniform icon slot would crop or distort it -- and single that family's
        card out, which is exactly what the owner's "no special treatment" ask
        ruled out.  The photo still has a real home: the family's own
        registration surfaces.  The catalogue draws only the generic doodle.
        """

        family = Course.objects.create(slug="campaign-art-family", title="Campaign art")
        cohort = Cohort.objects.create(
            course=family, slug="campaign-art", identifier="2026", title=family.title
        )
        RegistrationCampaign.objects.create(
            slug="campaign-art",
            title=family.title,
            current_course=cohort,
            hero_image_url="https://example.org/authored-cover.png",
        )
        response = self.client.get(reverse("course_list"))
        self.assertNotContains(response, "https://example.org/authored-cover.png")
        self.assertContains(response, static("core/illustrations/course-learning.webp"))
        self.assertNotContains(response, "mascot needed")

    def test_family_pages_and_catalogue_render_the_matching_static_files(self):
        for slug in COURSE_SLUGS:
            family, _ = Course.objects.get_or_create(slug=slug, defaults={"title": slug})
            Cohort.objects.create(
                course=family,
                slug=f"art-test-{slug}",
                identifier="art-test",
                title=family.title,
            )
            with self.subTest(slug=slug):
                response = self.client.get(reverse("course_family", args=[slug]))
                self.assertContains(response, static(f"core/illustrations/course-{slug}.webp"))

        response = self.client.get(reverse("course_list"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["hero_collage"])
        for card in response.context["hero_collage"]:
            self.assertContains(
                response, static(f"core/illustrations/course-{card['family_slug']}.webp")
            )
            self.assertContains(
                response,
                static(f"core/illustrations/course-{card['family_slug']}-dark.webp"),
            )
