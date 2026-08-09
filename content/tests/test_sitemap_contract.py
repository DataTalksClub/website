from __future__ import annotations

from django.test import SimpleTestCase

from content.public_views import production_sitemap
from content.sitemap_contract import (
    EXPECTED_SITEMAP_LOCATIONS,
    SitemapContractError,
    validate_sitemap_index,
)


class SitemapIndexContractTests(SimpleTestCase):
    def test_runtime_populated_index_matches_the_deployed_smoke_contract(self) -> None:
        self.assertEqual(
            validate_sitemap_index(production_sitemap().encode()),
            EXPECTED_SITEMAP_LOCATIONS,
        )

    def test_stale_empty_development_sitemap_is_rejected(self) -> None:
        stale_body = (
            b'<?xml version="1.0" encoding="UTF-8"?>\n'
            b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>\n'
        )
        with self.assertRaisesMessage(SitemapContractError, "sitemap index root differs"):
            validate_sitemap_index(stale_body)

    def test_malformed_unsafe_duplicate_and_noncanonical_indexes_are_rejected(self) -> None:
        accepted = production_sitemap().encode()
        first_entry = (
            b"<sitemap><loc>" + EXPECTED_SITEMAP_LOCATIONS[0].encode() + b"</loc></sitemap>"
        )
        cases = (
            ("malformed", b"<sitemapindex", "sitemap index is malformed"),
            (
                "stylesheet",
                b'<?xml-stylesheet type="text/xsl" href="https://example.com/a.xsl"?>' + accepted,
                "sitemap index contains an unsafe XML declaration",
            ),
            (
                "doctype",
                b"<!DOCTYPE sitemapindex [<!ENTITY unsafe SYSTEM 'file:///etc/passwd'>]>"
                + accepted,
                "sitemap index contains an unsafe XML declaration",
            ),
            (
                "duplicate",
                accepted.replace(b"</sitemapindex>", first_entry + b"</sitemapindex>"),
                "sitemap index contains duplicate locations",
            ),
            (
                "development origin",
                accepted.replace(b"https://datatalks.club", b"https://web.dtcdev.click", 1),
                "sitemap index location is not a canonical production URL",
            ),
            (
                "query",
                accepted.replace(b".xml</loc>", b".xml?source=unsafe</loc>", 1),
                "sitemap index location is not a canonical production URL",
            ),
            (
                "malformed port",
                accepted.replace(b"datatalks.club", b"datatalks.club:unsafe", 1),
                "sitemap index location is malformed",
            ),
            (
                "missing section",
                accepted.replace(first_entry, b"", 1),
                "sitemap index section locations differ",
            ),
        )
        for case, body, message in cases:
            with self.subTest(case=case), self.assertRaisesMessage(SitemapContractError, message):
                validate_sitemap_index(body)
