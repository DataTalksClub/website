from __future__ import annotations

import re
from pathlib import Path
from xml.etree import ElementTree

from django.test import TestCase, override_settings
from django.urls import reverse

from core.accessibility_registry import template_readability_issues
from core.source_policy import analytics_runtime_violations

ROOT = Path(__file__).resolve().parents[2]
LEGAL_ROUTES = {
    "terms": ("Terms of Service", "public/legal/terms.html"),
    "privacy": ("Privacy Policy", "public/legal/privacy.html"),
    "impressum": ("Impressum", "public/legal/impressum.html"),
}
FORBIDDEN_COPIED_PRODUCT_COPY = (
    "AI Shipping Labs",
    "Stripe",
    "paid membership",
    "Amazon SES",
    "Datamailer",
)
SITEMAP_NAMESPACE = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}


class LegalRouteTests(TestCase):
    def test_canonical_pages_are_public_readable_and_development_noindex(self) -> None:
        for route_name, (heading, template_name) in LEGAL_ROUTES.items():
            with self.subTest(route=route_name):
                path = reverse(route_name)
                self.assertEqual(path, f"/{route_name}")
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, template_name)
                self.assertTemplateUsed(response, "public/legal/base.html")
                self.assertTemplateUsed(response, "core/_site_footer.html")
                self.assertContains(response, f"<h1>{heading}</h1>", html=True)
                self.assertContains(
                    response,
                    f'<link rel="canonical" href="https://datatalks.club{path}">',
                    count=1,
                )
                self.assertContains(
                    response,
                    '<meta name="robots" content="noindex,nofollow">',
                    count=1,
                )
                self.assertEqual(
                    len(re.findall(r'<meta\s+name="description"', response.content.decode())),
                    1,
                )
                self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")
                self.assertContains(response, "Last updated: 11 August 2026")
                self.assertEqual(self.client.head(path).status_code, 200)
                self.assertEqual(self.client.post(path).status_code, 405)

    def test_slash_aliases_redirect_once_and_preserve_the_raw_query(self) -> None:
        query = "x=%2F&x=&q=A+B&q=A%20B"
        for route_name in LEGAL_ROUTES:
            source = f"/{route_name}/"
            target = f"/{route_name}"
            with self.subTest(source=source):
                response = self.client.get(f"{source}?{query}", follow=False)
                self.assertEqual(response.status_code, 301)
                self.assertEqual(response.headers["Location"], f"{target}?{query}")
                self.assertEqual(self.client.head(source, follow=False).status_code, 301)
                self.assertEqual(self.client.post(source, follow=False).status_code, 405)
                final = self.client.get(response.headers["Location"], follow=False)
                self.assertEqual(final.status_code, 200)
                self.assertNotIn("Location", final.headers)

    @override_settings(NOINDEX=False)
    def test_production_legal_pages_are_explicitly_indexable_and_self_canonical(self) -> None:
        for route_name in LEGAL_ROUTES:
            with self.subTest(route=route_name):
                response = self.client.get(reverse(route_name))
                self.assertEqual(response.status_code, 200)
                self.assertNotIn("X-Robots-Tag", response.headers)
                self.assertContains(
                    response,
                    '<meta name="robots" content="index,follow">',
                    count=1,
                )
                self.assertContains(
                    response,
                    f'href="https://datatalks.club/{route_name}"',
                    count=1,
                )

    def test_main_sitemap_lists_each_legal_canonical_once(self) -> None:
        response = self.client.get("/sitemaps/main.xml")
        self.assertEqual(response.status_code, 200)
        document = ElementTree.fromstring(response.content)
        locations = [
            node.text or ""
            for node in document.findall("s:url/s:loc", namespaces=SITEMAP_NAMESPACE)
        ]
        for route_name in LEGAL_ROUTES:
            canonical = f"https://datatalks.club/{route_name}"
            self.assertEqual(locations.count(canonical), 1)
            self.assertNotIn(f"{canonical}/", locations)


class LegalContentTests(TestCase):
    def test_terms_and_privacy_cover_current_dtc_flows_without_copied_product_copy(self) -> None:
        terms = self.client.get("/terms").content.decode()
        privacy = self.client.get("/privacy").content.decode()
        for value in (
            "DataTalks.Club",
            "member profile",
            "Slack",
            "events",
            "courses",
            "homework",
            "peer review",
            "certificates",
            "GitHub",
        ):
            with self.subTest(page="terms", value=value):
                self.assertIn(value.casefold(), terms.casefold())
        for value in (
            "controller",
            "Relay",
            "CloudFront",
            "CSRF",
            "country",
            "retention",
            "withdraw",
            "deletion",
            "minors",
            "180 days",
        ):
            with self.subTest(page="privacy", value=value):
                self.assertIn(value.casefold(), privacy.casefold())
        combined = f"{terms}\n{privacy}"
        for forbidden in FORBIDDEN_COPIED_PRODUCT_COPY:
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden.casefold(), combined.casefold())

    def test_impressum_keeps_unverified_identity_at_an_explicit_human_gate(self) -> None:
        response = self.client.get("/impressum")
        self.assertContains(response, "data-legal-review-required", count=1)
        self.assertContains(response, "Owner/legal verification required")
        self.assertContains(response, "Pending explicit owner/legal confirmation.", count=3)
        body = response.content.decode()
        for unverified_reference_value in (
            "Schönensche Str.",
            "DE343190995",
            "contact@aishippinglabs.com",
        ):
            self.assertNotIn(unverified_reference_value, body)

    def test_legal_templates_are_source_reviewed_readable_html(self) -> None:
        for relative in (
            "templates/core/_site_footer.html",
            "templates/public/legal/base.html",
            "templates/public/legal/terms.html",
            "templates/public/legal/privacy.html",
            "templates/public/legal/impressum.html",
        ):
            with self.subTest(template=relative):
                source = (ROOT / relative).read_text(encoding="utf-8")
                self.assertEqual(template_readability_issues(source), [])
                self.assertNotRegex(source, re.compile(r"<script[^>]*>.*</script>", re.DOTALL))


class SharedLegalFooterTests(TestCase):
    def test_every_representative_shell_uses_one_shared_footer_contract(self) -> None:
        for path in ("/", "/events", "/blog", "/courses", "/terms"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "core/_site_footer.html")
                body = response.content.decode()
                footer_match = re.search(r"<footer\b.*?</footer>", body, re.DOTALL)
                self.assertIsNotNone(footer_match)
                footer = footer_match.group(0) if footer_match else ""
                self.assertEqual(footer.count('aria-label="Legal"'), 1)
                self.assertEqual(footer.count('href="/terms"'), 1)
                self.assertEqual(footer.count('href="/privacy"'), 1)
                self.assertEqual(footer.count('href="/impressum"'), 1)
                self.assertEqual(body.count("data-analytics-preferences-open"), 1)
                self.assertIn('type="button"', body)
                self.assertNotIn('href="/terms/"', body)
                self.assertNotIn('href="/privacy/"', body)
                self.assertNotIn('href="/impressum/"', body)

    def test_one_base_shell_owns_the_shared_footer_partial(self) -> None:
        copied_base = (ROOT / "course_platform_templates/base.html").read_text(encoding="utf-8")
        public_base = (ROOT / "templates/core/base.html").read_text(encoding="utf-8")

        self.assertFalse((ROOT / "templates/base.html").exists())
        self.assertEqual(copied_base.count('{% include "core/_site_footer.html" %}'), 1)
        self.assertNotIn("site-footer-github", copied_base)
        self.assertNotIn("Analytics preferences</button>", copied_base)
        self.assertIn('{% extends "base.html" %}', public_base)
        self.assertNotIn('{% include "core/_site_footer.html" %}', public_base)

    def test_github_icon_and_ordinary_legal_link_styles_are_separate(self) -> None:
        footer = (ROOT / "templates/core/_site_footer.html").read_text(encoding="utf-8")
        shell = (ROOT / "core/static/core/site_shell.css").read_text(encoding="utf-8")
        self.assertIn('rel="noopener noreferrer"', footer)
        self.assertIn('aria-label="Website source on GitHub (opens in a new tab)"', footer)
        for state in ("", ":link", ":visited", ":hover", ":focus", ":active"):
            self.assertIn(f".site-footer .site-footer-github{state}", shell)
        self.assertIn("text-decoration: none !important", shell)
        self.assertIn(".site-footer-legal-control", shell)
        self.assertIn("text-decoration: underline", shell)

    def test_rendered_source_and_response_fail_closed_without_analytics_provider(self) -> None:
        response = self.client.get("/")
        self.assertFalse(response.cookies)
        html = response.content.decode()
        self.assertEqual(
            analytics_runtime_violations(html=html, request_urls=(), cookie_names=()),
            (),
        )
        script = (ROOT / "core/static/core/analytics_preferences.js").read_text(encoding="utf-8")
        self.assertNotRegex(script, r"https?://")
        self.assertNotRegex(script, r"\b(?:GTM-|G-[A-Z0-9]{8,}|UA-[0-9])")
        self.assertIn('var COOKIE_VERSION = "v1";', script)
        self.assertIn("60 * 60 * 24 * 180", script)
        self.assertIn('return window.location.protocol === "https:" ? "; Secure" : "";', script)
        self.assertEqual(script.count("dialog.show();"), 1)
        self.assertEqual(script.count("dialog.showModal();"), 1)
        self.assertIn('"dtc_attribution_"', script)
        self.assertIn("Domain=datatalks.club", script)
