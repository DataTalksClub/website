from __future__ import annotations

from urllib.parse import urlsplit
from xml.etree import ElementTree

from django.test import TestCase

from content.sitemap_contract import (
    EXPECTED_SITEMAP_LOCATIONS,
    PRODUCTION_ORIGIN,
    SITEMAP_NAMESPACE,
    validate_sitemap_index,
)
from courses.models import Cohort

URLSET_NS = {"s": SITEMAP_NAMESPACE}
COURSES_HUB = f"{PRODUCTION_ORIGIN}/courses"
COURSES_COHORT = f"{PRODUCTION_ORIGIN}/courses/ai-dev-tools-zoomcamp/cohorts/ai-dev-tools-2026"
VISIBLE_SLUGS = ("aa-visible-sitemap-2026", "zeta-visible-sitemap-2026")
HIDDEN_SLUG = "hidden-sitemap-2026"
PRIVATE_MARKERS = (
    "hidden-learner-secret@example.invalid",
    "private-registration-token-189",
    "homework-answer-key-189",
)


class CoursesSitemapContractTests(TestCase):
    def test_empty_course_table_emits_hub_and_cohort_only(self) -> None:
        self.assertEqual(Cohort.objects.count(), 0)

        locations = self._courses_sitemap_locations()

        self.assertEqual(locations, [COURSES_HUB, COURSES_COHORT])
        self._assert_index_and_sibling_remain_valid()

    def test_populated_visible_and_hidden_courses_follow_the_public_contract(self) -> None:
        Cohort.objects.create(
            slug=VISIBLE_SLUGS[1],
            title="Zeta Visible Sitemap Course",
            description="Public visible course used only for sitemap ordering.",
            visible=True,
        )
        Cohort.objects.create(
            slug=VISIBLE_SLUGS[0],
            title="AA Visible Sitemap Course",
            description="Public visible course created after the later slug.",
            visible=True,
        )
        Cohort.objects.create(
            slug=HIDDEN_SLUG,
            title="Hidden Sitemap Course",
            description=(
                "Do not publish hidden-learner-secret@example.invalid "
                "private-registration-token-189 homework-answer-key-189"
            ),
            visible=False,
        )

        locations = self._courses_sitemap_locations()

        self.assertEqual(
            locations,
            [
                COURSES_HUB,
                f"{PRODUCTION_ORIGIN}/courses/{VISIBLE_SLUGS[0]}",
                f"{PRODUCTION_ORIGIN}/courses/{VISIBLE_SLUGS[1]}",
                COURSES_COHORT,
            ],
        )
        self.assertNotIn(f"{PRODUCTION_ORIGIN}/courses/{HIDDEN_SLUG}", locations)
        self._assert_index_and_sibling_remain_valid()

    def _courses_sitemap_locations(self) -> list[str]:
        response = self.client.get("/sitemaps/courses.xml")
        self.assertEqual(response.status_code, 200)
        content_type = response.headers.get("Content-Type", "")
        self.assertEqual(content_type, "application/xml; charset=utf-8")
        self.assertNotIn("text/html", content_type)
        self.assertNotIn(b"<html", response.content.lower())
        self.assertEqual(response.headers.get("X-Robots-Tag"), "noindex, nofollow")

        try:
            root = ElementTree.fromstring(response.content)
        except ElementTree.ParseError as error:
            self.fail(f"courses sitemap is not well-formed XML: {error}")

        self.assertEqual(root.tag, f"{{{SITEMAP_NAMESPACE}}}urlset")
        locations = [node.text or "" for node in root.findall("s:url/s:loc", URLSET_NS)]
        self.assertEqual(locations, list(dict.fromkeys(locations)))
        for location in locations:
            parsed = urlsplit(location)
            self.assertEqual((parsed.scheme, parsed.netloc), ("https", "datatalks.club"))
            self.assertFalse(parsed.query or parsed.fragment)
            self.assertTrue(parsed.path.startswith("/courses"))
            self.assertFalse(parsed.path.endswith("/"))

        body = response.content.decode()
        lowered = body.lower()
        for marker in PRIVATE_MARKERS:
            self.assertNotIn(marker, body)
            self.assertNotIn(marker.lower(), lowered)
        return locations

    def _assert_index_and_sibling_remain_valid(self) -> None:
        index = self.client.get("/sitemap.xml")
        self.assertEqual(index.status_code, 200)
        self.assertEqual(
            index.headers.get("Content-Type"),
            "application/xml; charset=utf-8",
        )
        self.assertNotIn(b"<html", index.content.lower())
        self.assertEqual(
            validate_sitemap_index(index.content),
            EXPECTED_SITEMAP_LOCATIONS,
        )

        sibling = self.client.get("/sitemaps/main.xml")
        self.assertEqual(sibling.status_code, 200)
        self.assertEqual(
            sibling.headers.get("Content-Type"),
            "application/xml; charset=utf-8",
        )
        self.assertNotIn(b"<html", sibling.content.lower())
        sibling_root = ElementTree.fromstring(sibling.content)
        self.assertEqual(sibling_root.tag, f"{{{SITEMAP_NAMESPACE}}}urlset")
        sibling_locations = [
            node.text or "" for node in sibling_root.findall("s:url/s:loc", URLSET_NS)
        ]
        self.assertIn(f"{PRODUCTION_ORIGIN}/", sibling_locations)
        self.assertEqual(sibling_locations, list(dict.fromkeys(sibling_locations)))
