import hashlib
from pathlib import Path

from django.test import SimpleTestCase

from courses.registration import (
    COUNTRIES_BY_REGION,
    COUNTRY_CHOICES,
    ordered_countries,
    region_for_country,
    youtube_embed_url,
)


class RegistrationHelperTests(SimpleTestCase):
    def test_youtube_watch_url_becomes_embed_url(self):
        url = "https://www.youtube.com/watch?v=abc123&feature=share"

        result = youtube_embed_url(url)

        self.assertEqual(result, "https://www.youtube.com/embed/abc123")

    def test_youtu_be_url_becomes_embed_url(self):
        url = "https://youtu.be/abc123?si=tracking"

        result = youtube_embed_url(url)

        self.assertEqual(result, "https://www.youtube.com/embed/abc123")

    def test_non_youtube_url_is_unchanged(self):
        url = "https://videos.example.com/watch?v=abc123"

        result = youtube_embed_url(url)

        self.assertEqual(result, url)

    def test_empty_youtube_url_stays_empty(self):
        result = youtube_embed_url("")

        self.assertEqual(result, "")

    def test_country_helpers_use_countries_config(self):
        countries = ordered_countries()
        germany_region = region_for_country("Germany")

        self.assertIn("Germany", countries)
        self.assertEqual(germany_region, "Europe")
        self.assertEqual(COUNTRY_CHOICES[0], ("United States", "United States"))

    def test_reviewed_country_inventory_is_exact_and_region_scoped(self):
        country_path = Path(__file__).resolve().parents[1] / "countries.txt"
        self.assertEqual(
            hashlib.sha256(country_path.read_bytes()).hexdigest(),
            "a030b43fb91b368428b759e559fda4c859807a01d2ebb5d946841339bb1baae8",
        )

        expected_counts = {
            "Africa": 54,
            "North America": 32,
            "South America": 14,
            "Asia": 50,
            "Europe": 48,
            "Oceania": 17,
        }
        self.assertEqual(
            {region: len(countries) for region, countries in COUNTRIES_BY_REGION.items()},
            expected_counts,
        )

        country_rows = [
            (region, country)
            for region, countries in COUNTRIES_BY_REGION.items()
            for country in countries
        ]
        self.assertEqual(len(country_rows), 215)
        self.assertEqual(len({country for _region, country in country_rows}), 215)
        canonical_rows = "".join(f"{region}\t{country}\n" for region, country in country_rows)
        self.assertEqual(
            hashlib.sha256(canonical_rows.encode()).hexdigest(),
            "ad0f41850372b0884ee0116dc069c6890c7cbf1e6c9ea8f839f7bfa21a68c005",
        )

        expected_regions = {
            "Taiwan": "Asia",
            "Hong Kong": "Asia",
            "Macau": "Asia",
            "Gibraltar": "Europe",
            "Isle of Man": "Europe",
            "Guam": "Oceania",
            "Cook Islands": "Oceania",
            "British Virgin Islands": "North America",
            "U.S. Virgin Islands": "North America",
        }
        for country, region in expected_regions.items():
            with self.subTest(country=country):
                self.assertEqual(region_for_country(country), region)
                self.assertIn(country, ordered_countries())

        self.assertEqual(region_for_country("United States"), "North America")
        self.assertNotIn("United States of America", ordered_countries())
