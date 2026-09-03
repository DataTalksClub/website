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
FORBIDDEN_UNRELEASED_COPY = (
    "contact@aishippinglabs.com",
    "data-legal-review-required",
    "owner/legal",
    "owner/privacy",
    "source candidate",
    "this candidate",
    "pending explicit",
    "pending legal review",
    "verification required",
    "production acceptance",
    "acceptance gate",
    "review gate",
    "must be confirmed",
    "provisional schedule",
)
SITEMAP_NAMESPACE = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
MAIN_LANDMARK = re.compile(r"<main\b.*?</main>", re.DOTALL)


def _main_landmark(html: str) -> str:
    """Return the document a legal page renders, without the shell around it."""

    match = MAIN_LANDMARK.search(html)
    assert match is not None, "the page has no main landmark"
    return match.group(0)


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
                # The legal documents are text pages like any other (issue #179),
                # so the document they render is the shared one.
                self.assertTemplateUsed(response, "public/text_page.html")
                self.assertTemplateUsed(response, "core/_site_footer.html")
                self.assertContains(
                    response,
                    f'<h1 id="text-page-heading">{heading}</h1>',
                    html=True,
                )
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
        # The contract is about the documents' own words, so the scan reads the
        # main landmark rather than the whole response: since the legal pages
        # joined the design system (issue #179) each one carries the shared stylesheet
        # inline, and that stylesheet talks about striped avatars and player
        # stripes without either being copied product copy.
        combined = f"{_main_landmark(terms)}\n{_main_landmark(privacy)}"
        for forbidden in FORBIDDEN_COPIED_PRODUCT_COPY:
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden.casefold(), combined.casefold())

    def test_impressum_publishes_the_operator_identity_the_owner_supplied(self) -> None:
        """The gate this test used to hold is now the other way round.

        While the operator identity was unconfirmed, this test pinned the page at
        placeholders so nothing could be invented into it.  The owner has since
        supplied the details, so the same gate now runs in the opposite direction:
        the page has to carry each disclosure German law requires, and must not be
        able to fall back to a placeholder without failing here.
        """

        response = self.client.get("/impressum")
        body = response.content.decode()
        for published_detail in (
            # § 5 TMG: the operator, its address, and its representative.
            "DataTalks.Club",
            "Schonensche Straße 13",
            "10439 Berlin",
            "Deutschland",
            "Alexey Grigorev",
            # The contact this site publishes, never the other site's address.
            "mailto:alexey@datatalks.club",
            # § 27a UStG, and the person responsible for the content.
            "DE343190995",
            "Angaben gemäß § 5 TMG",
            "Umsatzsteuer-Identifikationsnummer",
            "Verantwortlich für den Inhalt nach § 55 Abs. 2 RStV",
            "Streitschlichtung",
            "Haftung für Inhalte",
            "Haftung für Links",
            "Urheberrecht",
        ):
            with self.subTest(published=published_detail):
                self.assertIn(published_detail, body)
        # The German disclosure is marked as German on an English page, so a
        # screen reader does not read it in the wrong voice.
        self.assertIn('<h2 lang="de">Angaben gemäß § 5 TMG</h2>', body)

    def test_impressum_sends_nobody_to_a_dispute_service_that_closed(self) -> None:
        """The ODR platform this text used to link to no longer exists.

        The European Commission's online dispute resolution platform ceased
        operation on 20 July 2025 under Regulation (EU) 2024/3228, so the referral
        that copied Impressum boilerplate still carries sends a visitor to nothing.
        The § 36 VSBG declaration underneath it is the part the statute still asks
        for, and it stays.
        """

        collapsed = " ".join(_main_landmark(self.client.get("/impressum").content.decode()).split())
        for closed in ("ec.europa.eu/consumers/odr", "Online-Streitbeilegung"):
            with self.subTest(closed=closed):
                self.assertNotIn(closed, collapsed)
        self.assertIn('<h2 lang="de">Streitschlichtung</h2>', collapsed)
        self.assertIn(
            "Wir sind nicht bereit oder verpflichtet, an Streitbeilegungsverfahren "
            "vor einer Verbraucherschlichtungsstelle teilzunehmen.",
            collapsed,
        )

    def test_no_legal_page_shows_a_visitor_a_placeholder_or_our_own_review_words(self) -> None:
        """A visitor must never read a sentence that was written for us.

        These are the exact phrasings the three documents used to carry while the
        operator identity was unconfirmed, plus the address that belongs to the
        other site the same operator runs.  None of them may come back.
        """

        for route_name in LEGAL_ROUTES:
            body = _main_landmark(self.client.get(f"/{route_name}").content.decode())
            for forbidden in FORBIDDEN_UNRELEASED_COPY:
                with self.subTest(route=route_name, forbidden=forbidden):
                    self.assertNotIn(forbidden.casefold(), body.casefold())

    def test_legal_templates_are_source_reviewed_readable_html(self) -> None:
        for relative in (
            "templates/core/_site_footer.html",
            "templates/public/text_page.html",
            "templates/public/legal/base.html",
            "templates/public/legal/terms.html",
            "templates/public/legal/privacy.html",
            "templates/public/legal/impressum.html",
        ):
            with self.subTest(template=relative):
                source = (ROOT / relative).read_text(encoding="utf-8")
                self.assertEqual(template_readability_issues(source), [])
                scripts = re.findall(r"<script[^>]*>.*?</script>", source, re.DOTALL)
                if relative == "templates/public/text_page.html":
                    # A legal page still runs no behaviour of its own.  The shared
                    # design system text page (issue #179) opens with the no-JavaScript
                    # class swap every page in the system carries, and that is the
                    # only script in it: everything else — the dark-mode bootstrap,
                    # the navigation and the footer scripts — comes from the shared
                    # shell partials rather than from this document.
                    self.assertEqual(len(scripts), 1)
                    self.assertIn('classList.remove("no-js")', scripts[0])
                else:
                    self.assertEqual(scripts, [])


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

    def test_one_shell_partial_owns_the_shared_footer_include(self) -> None:
        """One shell renders the footer, and no page keeps a second copy of it.

        The owner used to be `templates/site_base.html`; issue #179 finished porting
        the site to the design system, whose pages are complete documents that include
        `core/_site_shell_foot.html` rather than extending a base, so that partial is
        the single include site now.  The contract is unchanged: exactly one shell
        includes the partial, and no page template restates the footer's own markup.
        """

        shell_foot = (ROOT / "templates/core/_site_shell_foot.html").read_text(encoding="utf-8")
        page_roots = ("templates/public", "templates/review", "templates/core")

        self.assertFalse((ROOT / "templates/base.html").exists())
        self.assertFalse((ROOT / "templates/site_base.html").exists())
        self.assertFalse((ROOT / "templates/core/base.html").exists())
        self.assertEqual(shell_foot.count('{% include "core/_site_footer.html" %}'), 1)
        for root in page_roots:
            for path in sorted((ROOT / root).rglob("*.html")):
                if path.name in {"_site_footer.html", "_site_shell_foot.html"}:
                    continue
                with self.subTest(template=path.relative_to(ROOT).as_posix()):
                    source = path.read_text(encoding="utf-8")
                    self.assertNotIn('{% include "core/_site_footer.html" %}', source)
                    self.assertNotIn('<footer class="site-footer', source)
                    self.assertNotIn("Analytics preferences</button>", source)

    def test_github_icon_and_ordinary_legal_link_styles_are_separate(self) -> None:
        """The footer's icon link is undecorated; its word links stay underlined.

        These rules lived in `core/static/core/site_shell.css` while the site had an
        external shell stylesheet.  Design system pages load no stylesheet at all, so the
        footer's styling now comes from the shared `core/_design_system.html` block
        every page inlines, and the rules are read from there.  They no longer need
        `!important`, because there is no utility framework left to override.
        """

        footer = (ROOT / "templates/core/_site_footer.html").read_text(encoding="utf-8")
        design_system = (ROOT / "templates/core/_design_system.html").read_text(encoding="utf-8")
        self.assertIn('rel="noopener noreferrer"', footer)
        self.assertIn('aria-label="Website source on GitHub (opens in a new tab)"', footer)
        for state in ("", ":link", ":visited", ":hover", ":focus", ":active"):
            self.assertIn(f".site-footer .site-footer-github{state}", design_system)
        self.assertIn("text-decoration: none;", design_system)
        self.assertIn(".site-footer-legal-control", design_system)
        self.assertIn("text-decoration: underline;", design_system)

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
        self.assertNotIn("dialog.show();", script)
        self.assertEqual(script.count('dialog.setAttribute("open", "");'), 1)
        self.assertEqual(script.count("dialog.showModal();"), 1)
        self.assertIn('"dtc_attribution_"', script)
        self.assertIn("Domain=datatalks.club", script)

    def test_initial_consent_prompt_has_no_competing_status_role(self) -> None:
        footer = (ROOT / "templates/core/_site_footer.html").read_text(encoding="utf-8")
        self.assertNotIn('role="status"', footer)
        self.assertIn('aria-live="polite"', footer)
