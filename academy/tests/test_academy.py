"""The Academy pages: synced content, rendered in the site's own shell."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from community_base.content_sync.checkout import ImmutableCheckout
from community_base.content_sync.media import media_store
from community_base.content_sync.models import SyncStatus
from community_base.content_sync.orchestration import sync_content_source
from community_base.content_sync.parsers import get_parser
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import resolve

from academy.calendars import calendar_for
from academy.tests import content as fixture
from content.models import SyncedDocument
from content.sync_parsers.base import ContentParserError

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
#: Before the synthetic sessions (21 to 23 November 2026), so all three are upcoming.
BEFORE_SESSIONS = datetime(2026, 11, 1, tzinfo=UTC)
API = "https://augldvp3omxdxz3irqsmmxc25e0blpxx.lambda-url.eu-west-1.on.aws"

SITEMAP_PAGES = (
    "/academy/",
    "/academy/agentic-engineering/",
    "/academy/ai-engineering/",
    "/academy/guides/",
    "/academy/sessions/",
    "/academy/bildungsgutschein/",
    "/academy/faq/",
    "/academy/privacy/",
    "/academy/impressum/",
    "/academy/guides/synthetic/",
    "/academy/sessions/recap/",
)
ARTICLES = ("/academy/sessions/recap/", "/academy/guides/synthetic/")
ALL_PAGES = (*SITEMAP_PAGES, "/academy/confirm/", "/academy/sessions/materials/")
TEMPLATES = (
    "home",
    "program",
    "sessions",
    "materials",
    "confirm",
    "guide",
    "faq",
    "legal",
    "article",
    "articles",
)


def _clear_academy() -> None:
    SyncedDocument.objects.filter(content_kind__in=("academy_page", "academy_shared")).delete()
    SyncedDocument.objects.filter(
        content_kind="media", stable_key__startswith="images/academy/"
    ).delete()


class EmptyAcademyTests(TestCase):
    def setUp(self) -> None:
        _clear_academy()

    def test_an_empty_database_has_no_academy_and_no_fallback(self) -> None:
        for path in (
            *ALL_PAGES,
            "/academy/sessions/session-1.ics",
            "/academy/sitemap.xml",
            "/sitemaps/academy.xml",
            "/academy/layoff-proof/",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_the_raw_build_path_is_gone(self) -> None:
        self.assertFalse((REPOSITORY_ROOT / "academy/management").exists())
        for path in ("/academy/_astro/app.js", "/academy/favicon.svg", "/academy/robots.txt"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)


class AcademyPageTests(TestCase):
    def setUp(self) -> None:
        _clear_academy()
        fixture.publish()

    def test_every_page_renders_in_the_site_shell(self) -> None:
        for path in ALL_PAGES:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.resolver_match.app_name, "academy")
                body = response.content.decode()
                self.assertIn('<header class="masthead">', body)
                self.assertIn('class="band band-cream content-page-header', body)
                self.assertIn('class="band band-lavender content-page-content', body)
                self.assertIn("site-footer", body)
                self.assertEqual(body.count("<style>"), 1)
                self.assertNotIn('rel="stylesheet"', body)
                self.assertEqual(len(re.findall(r"<h1\b", body)), 1)
                # The site-wide Academy stripe would link the page to itself.
                self.assertNotIn("data-academy-cta", body)

    def test_pages_carry_no_academy_bar_and_one_footer(self) -> None:
        # The Academy sub-nav bar was removed (owner, 2026-11-24): the site
        # masthead's Academy link and the pages' own links do the navigating.
        for path in ALL_PAGES:
            with self.subTest(path=path):
                body = self.client.get(path).content.decode()
                self.assertNotIn("academy-bar", body)
                # One footer, the site's: no Academy link footer and no copyright line.
                self.assertNotIn("academy-links", body)
                self.assertNotIn("©", body)
                # Only an article, read like a blog post, carries the trail
                # back to its index.
                if path in ARTICLES:
                    self.assertIn('breadcrumbs" aria-label="Breadcrumb"', body)
                else:
                    self.assertNotIn('breadcrumbs" aria-label="Breadcrumb"', body)

    def test_home_keeps_its_anchors_and_the_funding_notice(self) -> None:
        home = self.client.get("/academy/").content.decode()
        for anchor in ('id="hero"', 'id="tracks"', 'id="facts"', 'id="funding"'):
            self.assertIn(anchor, home)
        funding = home[home.index('id="funding"') :]
        funding = funding[: funding.index("</section>")]
        self.assertIn("Synthetic home funding", funding)
        self.assertIn("Synthetic home funding text.", funding)
        self.assertIn('href="/academy/bildungsgutschein/">Synthetic home guide →</a>', funding)
        # The hero's one action points to the one waitlist form at the end.
        hero = home[: home.index("content-page-content")]
        self.assertIn('href="#signup-hero"', hero)
        self.assertNotIn("data-academy-signup", hero)
        self.assertIn('id="signup-hero"', home)
        program = self.client.get("/academy/agentic-engineering/").content.decode()
        self.assertIn('id="funding"', program)
        self.assertIn('href="/academy/bildungsgutschein/">Synthetic funding guide</a>', program)
        self.assertIn('href="https://example.invalid/funding">Synthetic funding link</a>', program)

    def test_every_pending_state_carries_the_confirmation_notice(self) -> None:
        notice = "Synthetic important: confirm your email or get nothing."
        # Each signup card says it in its form and again in its success state.
        for path, cards in (
            ("/academy/", 1),
            ("/academy/agentic-engineering/", 1),
            ("/academy/ai-engineering/", 1),
            ("/academy/sessions/", 1),
            ("/academy/sessions/materials/?session=1", 1),
        ):
            with self.subTest(path=path):
                body = self.client.get(path).content.decode()
                # Only after submitting: the empty form does not carry it
                # (owner, 2026-11-24), the success state does.
                self.assertEqual(body.count("data-confirm-notice"), cards)
                self.assertEqual(body.count(notice), cards)
                for form in body.split("<form ")[1:]:
                    self.assertNotIn(notice, form.split("</form>")[0])
                for success in body.split("data-success hidden")[1:]:
                    self.assertIn(notice, success.split("</div>\n  </div>")[0])

    def test_program_pages_close_with_the_other_track_and_no_sticky_bar(self) -> None:
        program = self.client.get("/academy/ai-engineering/").content.decode()
        # The floating waitlist bar was removed (owner, 2026-11-24).
        self.assertNotIn("data-academy-sticky", program)
        # The questions come last, right before the closing waitlist.
        self.assertLess(program.index('id="related-heading"'), program.index('id="faq-heading"'))
        self.assertLess(program.index('id="faq-heading"'), program.index('id="join-heading"'))
        self.assertIn("Synthetic other track?", program)
        self.assertIn('href="/academy/agentic-engineering/">Agentic Engineering</a>', program)
        self.assertIn(f'<a href="mailto:{fixture.CONTACT}">', program)
        for path in ("/academy/", "/academy/sessions/", "/academy/faq/"):
            self.assertNotIn("data-academy-sticky", self.client.get(path).content.decode())

    def test_the_sessions_page_opens_on_its_form(self) -> None:
        body = self.client.get("/academy/sessions/").content.decode()
        header = body[: body.index("content-page-content")]
        self.assertIn('href="#register"', header)
        self.assertEqual(header.count('<li class="list-row">'), 3)
        # The form is the first chapter under the header, and the page's only one.
        content = body[body.index("content-page-content") :]
        first = content[: content.index("</section>")]
        self.assertIn('id="final-heading"', first)
        self.assertIn('id="register"', first)
        self.assertEqual(body.count("data-academy-sessions"), 1)
        self.assertEqual(body.count('class="list-row academy-session"'), 3)

    def test_the_german_guide_points_to_the_english_page(self) -> None:
        guide = self.client.get("/academy/bildungsgutschein/").content.decode()
        self.assertIn('<html lang="de"', guide)
        note = guide[guide.index('class="callout callout-info academy-english"') :]
        note = note[: note.index("</p>")]
        self.assertIn('lang="en"', note)
        self.assertIn("Synthetic: for English, check this page:", note)
        self.assertIn('href="/academy/#funding">Synthetic English page</a>', note)

    def test_an_article_reads_as_prose_and_closes_on_the_waitlist(self) -> None:
        response = self.client.get("/academy/sessions/recap/")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        header = body[: body.index("content-page-content")]
        self.assertIn('<h1 id="academy-heading">Synthetic recap heading</h1>', header)
        self.assertIn("Synthetic recap lead.", header)
        self.assertIn('<time datetime="2026-11-23">Nov 23, 2026</time>', header)
        self.assertIn("1 min read", header)
        self.assertIn("Synthetic Author", header)
        self.assertIn('href="/academy/guides/">Synthetic articles index</a>', header)
        content = body[body.index("content-page-content") :]
        self.assertIn("<h2>Synthetic recap section</h2>", content)
        self.assertIn('<a href="/academy/ai-engineering/">a program</a>', content)
        self.assertIn("<li>synthetic point</li>", content)
        # A marked standalone link in the body is the shared secondary action.
        self.assertIn(
            '<p class="prose-action"><a class="cta cta-secondary interactive-lift" '
            'href="https://example.invalid/apply" rel="noopener noreferrer">'
            "Synthetic body action</a></p>",
            content,
        )
        self.assertNotIn("{.button}", body)
        self.assertIn('href="/academy/agentic-engineering/">Synthetic AE program</a>', content)
        self.assertIn('href="https://example.invalid/post">Synthetic outside link</a>', content)
        # A table scrolls inside the system's named frame.
        self.assertIn(
            '<div class="prose-scroll" role="region" tabindex="0" aria-label="Table 1"><table>',
            content,
        )
        self.assertIn('id="faq-1"', content)
        self.assertIn("Synthetic recap question?", content)
        # One closing action, to the waitlist card on the home page.
        closing = content[content.index('id="join-heading"') :]
        closing = closing[: closing.index("</section>")]
        self.assertIn("Synthetic recap closing text.", closing)
        self.assertIn('href="/academy/#signup-hero"', closing)
        self.assertIn("Join the synthetic list", closing)
        self.assertEqual(content.count('id="join-heading"'), 1)
        self.assertNotIn("data-academy-signup", body)
        self.assertContains(
            response,
            '<link rel="canonical" href="https://datatalks.club/academy/sessions/recap/">',
            html=False,
        )
        self.assertNotContains(response, "noindex")
        documents = [
            json.loads(match)
            for match in re.findall(r'<script type="application/ld\+json">(.*?)</script>', body)
        ]
        self.assertEqual([document["@type"] for document in documents], ["FAQPage", "Article"])
        self.assertEqual(documents[0]["mainEntity"][0]["name"], "Synthetic recap question?")
        article = documents[1]
        self.assertEqual(article["headline"], "Synthetic recap heading")
        self.assertEqual(article["datePublished"], "2026-11-23")
        self.assertEqual(article["author"], {"@type": "Person", "name": "Synthetic Author"})
        self.assertEqual(article["url"], "https://datatalks.club/academy/sessions/recap/")

    def test_an_article_names_its_own_closing_action(self) -> None:
        body = self.client.get("/academy/guides/synthetic/").content.decode()
        closing = body[body.index('id="join-heading"') :]
        closing = closing[: closing.index("</section>")]
        self.assertIn('href="/academy/ai-engineering/#signup-hero"', closing)
        self.assertIn("Synthetic guide action", closing)
        self.assertNotIn('id="faq-heading"', body)

    def test_the_articles_index_and_the_home_list_every_article_newest_first(self) -> None:
        index = self.client.get("/academy/guides/").content.decode()
        self.assertIn("Synthetic articles index", index)
        self.assertLess(
            index.index('href="/academy/guides/synthetic/"'),
            index.index('href="/academy/sessions/recap/"'),
        )
        self.assertEqual(index.count('class="list-row archive-row'), 2)
        home = self.client.get("/academy/").content.decode()
        articles = home[home.index('id="articles"') :]
        articles = articles[: articles.index("</section>")]
        self.assertIn('href="/academy/guides/">Synthetic all articles →</a>', articles)
        self.assertIn("Synthetic guide heading", articles)
        self.assertIn("Synthetic recap heading", articles)

    def test_german_and_english_guides_declare_each_other(self) -> None:
        guide = self.client.get("/academy/bildungsgutschein/").content.decode()
        self.assertIn(
            '<link rel="alternate" hreflang="de" href="https://datatalks.club/academy/bildungsgutschein/">',
            guide,
        )
        self.assertIn(
            '<link rel="alternate" hreflang="en" href="https://datatalks.club/academy/guides/synthetic/">',
            guide,
        )

    def test_a_session_links_its_recap_and_the_faq_closes_on_the_waitlist(self) -> None:
        sessions = self.client.get("/academy/sessions/").content.decode()
        cards = sessions.split('class="list-row academy-session"')[1:]
        self.assertIn('href="/academy/sessions/recap/">Synthetic recap</a>', cards[0])
        self.assertNotIn('class="academy-session-recap"', cards[1] + cards[2])
        faq = self.client.get("/academy/faq/").content.decode()
        closing = faq[faq.index('id="join-heading"') :]
        closing = closing[: closing.index("</section>")]
        self.assertIn("Synthetic FAQ closing text.", closing)
        self.assertIn('href="/academy/#signup-hero"', closing)

    def test_the_optional_copy_draws_nothing_when_absent(self) -> None:
        pages = fixture.pages_copy()
        del pages["index"][0]["funding"]
        del pages["bildungsgutschein"][0]["english"]
        del pages["faq"][0]["join"]
        del pages["sessions-recap"][0]["related"]
        del pages["sessions-recap"][0]["faq"]
        for program in ("agentic-engineering", "ai-engineering"):
            for key in ("related", "overview", "capstone"):
                del pages[program][0][key]
        del pages["sessions"][0]["sessions"][0]["recap"]
        shared = {
            key: value
            for key, value in fixture.SHARED.items()
            if key
            not in {
                "header",
                "sticky",
                "contact",
                "other_track",
                "confirm_notice",
                "funding",
                "interviews",
                "schedule",
            }
        }
        _clear_academy()
        fixture.publish(fixture.academy_tree(pages=pages, shared=shared))
        for path in ALL_PAGES:
            with self.subTest(path=path):
                body = self.client.get(path).content.decode()
                self.assertEqual(self.client.get(path).status_code, 200)
                for marker in (
                    'id="funding"',
                    'id="funding-heading"',
                    "data-academy-sticky",
                    "data-confirm-notice",
                    'class="callout callout-info academy-english"',
                    'class="academy-contact"',
                    'class="academy-other-track"',
                    'class="academy-session-recap"',
                    'id="related-heading"',
                    'id="overview-heading"',
                    'id="interviews-heading"',
                    'id="schedule"',
                ):
                    self.assertNotIn(marker, body)
        self.assertNotIn('id="join-heading"', self.client.get("/academy/faq/").content.decode())

    def test_canonical_open_graph_and_language(self) -> None:
        response = self.client.get("/academy/agentic-engineering/")
        self.assertContains(
            response,
            '<link rel="canonical" href="https://datatalks.club/academy/agentic-engineering/">',
            html=False,
        )
        self.assertContains(response, "<title>")
        self.assertContains(response, "Agentic Engineering synthetic title")
        self.assertContains(response, '<meta property="og:site_name" content="Synthetic Academy">')
        self.assertContains(
            response,
            '<meta property="og:url" content="https://datatalks.club/academy/agentic-engineering/">',
        )
        self.assertContains(
            response,
            '<meta property="og:image" content="https://datatalks.club/images/academy/og/agentic-engineering.png">',
        )
        self.assertContains(response, '<html lang="en"')
        german = self.client.get("/academy/bildungsgutschein/")
        self.assertContains(german, '<html lang="de"')
        self.assertContains(
            german,
            '<link rel="alternate" hreflang="de" href="https://datatalks.club/academy/bildungsgutschein/">',
        )
        self.assertContains(self.client.get("/academy/impressum/"), '<html lang="de"')
        self.assertContains(
            self.client.get("/academy/confirm/"), '<meta name="robots" content="noindex">'
        )
        self.assertNotContains(self.client.get("/academy/faq/"), 'content="noindex"')

    def test_structured_data_follows_the_content(self) -> None:
        body = self.client.get("/academy/ai-engineering/").content.decode()
        documents = [
            json.loads(match)
            for match in re.findall(r'<script type="application/ld\+json">(.*?)</script>', body)
        ]
        kinds = [document["@type"] for document in documents]
        self.assertEqual(kinds, ["Course", "FAQPage"])
        answers = [item["acceptedAnswer"]["text"] for item in documents[1]["mainEntity"]]
        # Markdown links read as their label; a missing answer is the prerequisites.
        self.assertEqual(
            answers, ["See the privacy notice.", "AI Engineering synthetic prerequisites."]
        )
        sessions = self.client.get("/academy/sessions/").content.decode()
        self.assertEqual(sessions.count('"@type":"Event"'), 3)
        self.assertIn('"startDate":"2026-11-21T17:00:00+01:00"', sessions)

    def test_copy_is_the_synced_content(self) -> None:
        home = self.client.get("/academy/")
        for text in (
            "Synthetic home headline",
            "Synthetic home lead.",
            "Read about Agentic Engineering",
            "Agentic Engineering highlight 1.",
            "Agentic Engineering highlight 3.",
            "Synthetic instructor line.",
            "Synthetic home sessions",
            "Synthetic session 1, a talk",
            "Synthetic FAQ question?",
        ):
            self.assertContains(home, text)
        # The card shows the program's own highlights, not picked outcomes.
        self.assertNotContains(home, "Agentic Engineering outcome 1.")
        program = self.client.get("/academy/agentic-engineering/")
        for text in (
            "Agentic Engineering synthetic first fit.",
            "Synthetic shared fit.",
            "Agentic Engineering module three.",
            "Synthetic module description.",
            "Agentic Engineering synthetic overview paragraph.",
            "Agentic Engineering synthetic outcomes intro.",
            "Agentic Engineering capstone.",
            "Synthetic drills.",
            "Synthetic schedule text.",
            "Agentic Engineering synthetic related",
            '<a href="https://example.invalid/courses">synthetic courses</a>',
            '<a href="/academy/privacy/">privacy notice</a>',
            "Synthetic other track?",
            'href="/academy/ai-engineering/"',
        ):
            self.assertContains(program, text, html=False)
        self.assertContains(self.client.get("/academy/impressum/"), "Synthetische Straße 1<br>")
        self.assertContains(self.client.get("/academy/privacy/"), "<code>utm_*</code>")

    def test_the_syllabus_reads_week_by_week(self) -> None:
        body = self.client.get("/academy/agentic-engineering/").content.decode()
        syllabus = body[body.index('id="syllabus-heading"') :]
        syllabus = syllabus[: syllabus.index("</section>")]
        # Weeks 1 and 2; the two modules taught in week 2 share one row, even
        # across the phase boundary only the first row names the week.
        self.assertEqual(syllabus.count("Synthetic week 1"), 1)
        self.assertEqual(syllabus.count("Synthetic week 2"), 2)
        self.assertNotIn("Weeks 1 to 2", body)
        project = body[body.index('id="project-heading"') :]
        project = project[: project.index("</section>")]
        self.assertIn("Synthetic week 3", project)
        self.assertIn("Agentic Engineering synthetic project.", project)
        interviews = body[body.index('id="interviews-heading"') :]
        interviews = interviews[: interviews.index("</section>")]
        self.assertIn("Synthetic week 4", interviews)
        self.assertIn("Synthetic week 5", interviews)

    def test_a_past_session_links_its_recap_and_is_no_longer_offered(self) -> None:
        after = datetime(2026, 11, 22, 12, tzinfo=UTC)
        with mock.patch("academy.presentation.timezone.now", return_value=after):
            body = self.client.get("/academy/sessions/").content.decode()
        header = body[: body.index("content-page-content")]
        self.assertIn('href="/academy/sessions/recap/">Synthetic recap</a>', header)
        self.assertNotIn('name="sessions" value="session-1"', body)
        self.assertIn('name="sessions" value="session-3"', body)

    @mock.patch("academy.presentation.timezone.now", return_value=BEFORE_SESSIONS)
    def test_forms_carry_the_backend_contract(self, _now: mock.Mock) -> None:
        home = self.client.get("/academy/").content.decode()
        self.assertEqual(home.count("data-academy-signup"), 1)
        self.assertIn(f'data-api-origin="{API}"', home)
        self.assertIn('data-consent-version="2026-11-13.v1"', home)
        self.assertIn('name="program"', home)
        self.assertIn(
            '<option value="agentic-engineering" data-survey-option="A. Agentic Engineering">', home
        )
        for name in ('name="name"', 'name="payment_plan"', 'name="email"', 'name="website"'):
            self.assertIn(name, home)
        program = self.client.get("/academy/ai-engineering/").content.decode()
        self.assertEqual(program.count("data-academy-signup"), 1)
        self.assertIn('data-track="ai-engineering"', program)
        sessions = self.client.get("/academy/sessions/").content.decode()
        for name in (
            'name="sessions" value="session-1"',
            'name="track_interest" value="agentic"',
            'name="status" value="job_hunting"',
            'name="years_dev"',
            'name="based_in" value="outside"',
            'name="bildungsgutschein" value="dont_know"',
            'name="free_text"',
            'name="academy_optin" value="yes"',
            'data-form-kind="main"',
            'href="/academy/sessions/session-2.ics"',
            'href="/academy/sessions/synthetic-series.ics"',
        ):
            self.assertIn(name, sessions)
        materials = self.client.get("/academy/sessions/materials/?session=1").content.decode()
        self.assertIn('data-form-kind="qr"', materials)
        self.assertIn('data-session-ids="session-1 session-2 session-3"', materials)
        self.assertNotIn('name="sessions"', materials)

    def test_headers_methods_and_token_privacy(self) -> None:
        response = self.client.get("/academy/confirm/?token=opaque-test-token")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn("private", response["Cache-Control"])
        self.assertNotContains(response, "opaque-test-token")
        connect = response["Content-Security-Policy"].split("connect-src ")[1].split(";")[0].split()
        self.assertIn(f"{API}/signup", connect)
        self.assertIn(f"{API}/confirm", connect)
        self.assertNotIn(API, connect)
        self.assertNotIn(f"{API}/pv", connect)
        self.assertEqual(self.client.head("/academy/").content, b"")
        self.assertEqual(self.client.post("/academy/").status_code, 405)
        self.assertEqual(self.client.post("/academy/confirm/").status_code, 405)
        self.assertEqual(self.client.get("/academy/unknown/").status_code, 404)
        self.assertEqual(self.client.get("/academy/sessions/unknown.ics").status_code, 404)
        self.assertEqual(resolve("/academy/").app_name, "academy")

    @override_settings(ALLOWED_HOSTS=["prod.datatalks.club"])
    def test_analytics_follow_the_shared_consent_and_skip_tokens(self) -> None:
        from core.source_policy import umami_public_path

        for path in SITEMAP_PAGES:
            self.assertTrue(umami_public_path(path), path)
        self.assertFalse(umami_public_path("/academy/confirm/"))
        self.assertFalse(umami_public_path("/academy/sessions/materials/"))
        self.assertTrue(umami_public_path("/academy/guides/synthetic-guide/"))
        home = self.client.get("/academy/", HTTP_HOST="prod.datatalks.club")
        self.assertContains(home, 'data-umami-page-path="/academy/"')
        confirm = self.client.get(
            "/academy/confirm/?token=opaque-test-token", HTTP_HOST="prod.datatalks.club"
        )
        self.assertNotContains(confirm, "data-umami-script-url")
        self.assertNotContains(confirm, "opaque-test-token")

    def test_sitemap_and_its_mirror(self) -> None:
        response = self.client.get("/academy/sitemap.xml")
        self.assertEqual(response["Content-Type"], "application/xml; charset=utf-8")
        locations = re.findall(r"<loc>(.*?)</loc>", response.content.decode())
        self.assertEqual(locations, [f"https://datatalks.club{path}" for path in SITEMAP_PAGES])
        self.assertEqual(self.client.get("/sitemaps/academy.xml").content, response.content)

    def test_calendars_and_the_historical_redirect(self) -> None:
        one = self.client.get("/academy/sessions/session-2.ics")
        self.assertEqual(one["Content-Type"], "text/calendar; charset=utf-8")
        text = one.content.decode()
        self.assertTrue(
            text.startswith("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Synthetic//Series//EN")
        )
        self.assertIn("UID:session-2@series.example.invalid\r\n", text)
        self.assertIn("DTSTART:20261122T160000Z\r\n", text)
        self.assertIn("SUMMARY:Synthetic Series #2: Synthetic session 2\\, a talk\r\n", text)
        self.assertEqual(text.count("BEGIN:VEVENT"), 1)
        everything = self.client.get("/academy/sessions/synthetic-series.ics").content.decode()
        self.assertEqual(everything.count("BEGIN:VEVENT"), 3)
        redirect = self.client.get("/academy/layoff-proof/?utm_source=x&utm_content=b")
        self.assertEqual(redirect.status_code, 301)
        self.assertEqual(redirect["Location"], "/academy/sessions/?utm_source=x&utm_content=b")

    def test_the_retired_build_images_move_permanently(self) -> None:
        moved = (
            "og/home.png",
            "og/agentic-engineering.png",
            "og/ai-engineering.png",
            "og/sessions.png",
            "qr/session-1.png",
            "qr/session-1.svg",
            "qr/session-2.png",
            "qr/session-2.svg",
            "qr/session-3.png",
            "qr/session-3.svg",
        )
        for name in moved:
            with self.subTest(name=name):
                response = self.client.get(f"/academy/{name}")
                self.assertEqual(response.status_code, 301)
                self.assertEqual(response["Location"], f"/images/academy/{name}")
        for name in ("og/other.png", "qr/session-4.png", "og/home.svg", "qr/session-1.jpg"):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(f"/academy/{name}").status_code, 404)
        self.assertEqual(self.client.head("/academy/og/home.png").status_code, 301)
        self.assertEqual(self.client.post("/academy/og/home.png").status_code, 405)

    def test_materials_need_the_sessions_page(self) -> None:
        SyncedDocument.objects.filter(
            content_kind="academy_page", stable_key="/academy/sessions/"
        ).delete()
        self.assertEqual(self.client.get("/academy/sessions/materials/").status_code, 404)


class CalendarFormatTests(SimpleTestCase):
    def test_text_escaping_and_folding_follow_rfc_5545(self) -> None:
        page = fixture.PAGES["sessions"][0]
        session = {**page["sessions"][0], "title": "Ä long title; with, commas " + "x" * 80}
        text = calendar_for(page, [session])
        lines = text.split("\r\n")
        self.assertTrue(text.endswith("END:VCALENDAR\r\n"))
        for line in lines:
            self.assertLessEqual(len(line.encode()), 75)
        unfolded = text.replace("\r\n ", "")
        self.assertIn("SUMMARY:Synthetic Series #1: Ä long title; with\\, commas", unfolded)
        self.assertIn(
            "Zoom link: https://example.invalid/zoom/1\\n\\n"
            "Synthetic calendar note\\, with a comma.",
            unfolded,
        )


class AcademyParserTests(TestCase):
    def setUp(self) -> None:
        _clear_academy()
        self.source = fixture.editorial_source()

    def discover(self, tree: dict[str, bytes], kind: str = "academy_page"):
        with ImmutableCheckout(fixture.write_tree(tree), commit_sha="b" * 40) as checkout:
            return get_parser(kind).discover(checkout, self.source)

    def test_pages_and_shared_copy_publish_at_their_paths(self) -> None:
        items = self.discover(fixture.academy_tree())
        self.assertEqual([item.key for item in items], sorted(ALL_PAGES))
        record = next(item for item in items if item.key == "/academy/").data["record"]
        self.assertEqual(record["layout"], "home")
        self.assertEqual(record["og_image"], "/images/academy/og/home.png")
        self.assertEqual(record["provenance"]["repository"], "DataTalksClub/content")
        shared = self.discover(fixture.academy_tree(), "academy_shared")
        self.assertEqual([item.key for item in shared], ["shared"])
        media = [item.key for item in self.discover(fixture.academy_tree(), "media")]
        self.assertIn("images/academy/og/home.png", media)
        self.assertIn("images/academy/qr/session-1.svg", media)

    def test_a_repository_without_academy_publishes_nothing(self) -> None:
        self.assertEqual(self.discover({"slack.yaml": b"title: x\n"}), [])
        self.assertEqual(self.discover({"slack.yaml": b"title: x\n"}, "academy_shared"), [])

    def test_invalid_pages_are_refused(self) -> None:
        cases = {
            "unknown layout": lambda pages: pages["faq"][0].update(layout="landing"),
            "unknown field": lambda pages: pages["faq"][0].update(extra="x"),
            "missing field": lambda pages: pages["faq"][0].pop("lead"),
            "missing image": lambda pages: pages["index"][0].update(
                og_image="images/academy/none.png"
            ),
            "outside path": lambda pages: pages["faq"][0].update(path="/faq/"),
            "unsafe link": lambda pages: pages["index"][0]["hero"]["links"][0].update(
                href="javascript:x"
            ),
            "protocol-relative link": lambda pages: pages["index"][0]["hero"]["links"][0].update(
                href="//evil.example/"
            ),
            "Academy path that does not exist": lambda pages: pages["index"][0]["hero"]["links"][
                0
            ].update(href="/academy/a/b/c/d/"),
            "program path": lambda pages: pages["ai-engineering"][0].update(path="/academy/other/"),
            "body on a structured page": lambda pages: pages.update(faq=(pages["faq"][0], "Body")),
            "duplicate session": lambda pages: pages["sessions"][0]["sessions"][1].update(
                id="session-1"
            ),
            "article without a body": lambda pages: pages.update(
                {"sessions-recap": (pages["sessions-recap"][0], "")}
            ),
            "article date": lambda pages: pages["sessions-recap"][0].update(published="2026-02-30"),
            "article date shape": lambda pages: pages["sessions-recap"][0].update(
                published="23 Nov 2026"
            ),
            "article without its closing": lambda pages: pages["sessions-recap"][0].pop("join"),
            "unsafe recap link": lambda pages: pages["sessions"][0]["sessions"][0]["recap"].update(
                href="javascript:x"
            ),
            "syllabus not from week 1": lambda pages: pages["ai-engineering"][0]["syllabus"][0][
                "modules"
            ][0].update(week=2),
            "skipped week": lambda pages: pages["ai-engineering"][0]["capstone"]["modules"][
                0
            ].update(week=5),
            "week missing": lambda pages: pages["ai-engineering"][0]["syllabus"][0]["modules"][
                0
            ].pop("week"),
            "unsafe closing link": lambda pages: pages["guides-synthetic"][0]["join"].update(
                href="javascript:x"
            ),
            "alternate outside the Academy": lambda pages: pages["bildungsgutschein"][0][
                "alternates"
            ][0].update(path="/blog/"),
            "articles index field": lambda pages: pages["guides"][0].update(items=[]),
        }
        for name, change in cases.items():
            with self.subTest(case=name):
                pages = fixture.pages_copy()
                change(pages)
                with self.assertRaises(ContentParserError):
                    self.discover(fixture.academy_tree(pages=pages))

    def test_shared_interview_weeks_must_run_on(self) -> None:
        shared = json.loads(json.dumps(fixture.SHARED))
        shared["interviews"]["modules"][1]["week"] = 7
        with self.assertRaises(ContentParserError):
            self.discover(fixture.academy_tree(shared=shared), "academy_shared")

    def test_pages_without_the_shared_copy_are_refused(self) -> None:
        tree = fixture.academy_tree()
        del tree["academy/shared.yaml"]
        with self.assertRaises(ContentParserError):
            self.discover(tree)

    def test_upsert_is_idempotent(self) -> None:
        tree = fixture.academy_tree()
        for expected in ("created", "unchanged"):
            with ImmutableCheckout(fixture.write_tree(tree), commit_sha="c" * 40) as checkout:
                parser = get_parser("academy_page")
                actions = {
                    parser.upsert(item, self.source, media_store()).action
                    for item in parser.discover(checkout, self.source)
                }
            self.assertEqual(actions, {expected})
        self.assertEqual(
            SyncedDocument.objects.filter(content_kind="academy_page").count(), len(ALL_PAGES)
        )


class AcademySyncTests(TestCase):
    """The supported entry point, ``sync_content_source``, over a local checkout."""

    def setUp(self) -> None:
        SyncedDocument.objects.filter(source__slug="dtc-content").delete()
        self.source = fixture.editorial_source()
        self.repository = fixture.git_repository(fixture.academy_tree())

    def test_sync_publishes_the_academy_and_a_second_sync_changes_nothing(self) -> None:
        log = sync_content_source(self.source, repo_dir=str(self.repository), force=True)
        self.assertEqual(log.status, SyncStatus.SUCCESS, log.errors)
        self.assertEqual(self.client.get("/academy/").status_code, 200)
        before = dict(
            SyncedDocument.objects.filter(source=self.source).values_list("stable_key", "checksum")
        )
        log = sync_content_source(self.source, repo_dir=str(self.repository), force=True)
        self.assertEqual(log.status, SyncStatus.SUCCESS, log.errors)
        self.assertEqual((log.items_created, log.items_updated, log.items_deleted), (0, 0, 0))
        after = dict(
            SyncedDocument.objects.filter(source=self.source).values_list("stable_key", "checksum")
        )
        self.assertEqual(after, before)
        self.assertIn("images/academy/portrait.jpg", after)


class AcademyTemplateContractTests(SimpleTestCase):
    def test_every_page_extends_the_shared_content_page(self) -> None:
        for name in TEMPLATES:
            with self.subTest(template=name):
                source = (REPOSITORY_ROOT / f"academy/templates/academy/{name}.html").read_text()
                self.assertIn('{% extends "core/content_page.html" %}', source)
                self.assertNotIn("<!DOCTYPE html>", source)
                self.assertNotIn('class="band ', source)
                self.assertIn('{% include "academy/_styles.html" %}', source)

    def test_templates_stay_readable(self) -> None:
        from core.accessibility_registry import template_readability_issues

        failures = []
        for path in sorted((REPOSITORY_ROOT / "academy/templates/academy").glob("*.html")):
            failures.extend(
                f"{path.name}:{issue}" for issue in template_readability_issues(path.read_text())
            )
        self.assertEqual(failures, [])
