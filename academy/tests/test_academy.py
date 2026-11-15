import hashlib
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import resolve

from academy.models import PublishedResource


class AcademyTests(TestCase):
    def publish(self, path: str, body: bytes, content_type: str = "text/html") -> None:
        PublishedResource.objects.create(
            path=path,
            content=body,
            content_type=content_type,
            source_commit="a" * 40,
            sha256=hashlib.sha256(body).hexdigest(),
        )

    def test_empty_database_has_no_page_or_asset_fallback(self) -> None:
        for path in ("", "sessions/", "_astro/missing.js", "sitemap.xml"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get("/academy/" + path).status_code, 404)

    def test_pages_metadata_assets_and_calendars_are_served_verbatim(self) -> None:
        pages = (
            "",
            "agentic-engineering/",
            "ai-engineering/",
            "sessions/",
            "bildungsgutschein/",
            "faq/",
            "privacy/",
            "impressum/",
            "sessions/materials/",
            "confirm/",
            "layoff-proof/",
        )
        for path in pages:
            body = (
                f'<title>Source {path}</title><link rel="canonical" '
                f'href="https://datatalks.club/academy/{path}">'
                '<meta property="og:site_name" content="DataTalks.Club Academy">'
            ).encode()
            self.publish(path, body)
            response = self.client.get("/academy/" + path)
            self.assertEqual(response.content, body)
            self.assertEqual(response.resolver_match.app_name, "academy")
            self.assertIn("lambda-url.eu-west-1.on.aws", response["Content-Security-Policy"])
            self.assertIn("umami.169.58.119.163.sslip.io", response["Content-Security-Policy"])
        for path, mime, body in (
            ("_astro/app.js", "text/javascript", b"source script"),
            ("sessions/session-1.ics", "text/calendar", b"BEGIN:VCALENDAR"),
            ("font.woff2", "font/woff2", b"font bytes"),
        ):
            self.publish(path, body, mime)
            response = self.client.get("/academy/" + path)
            self.assertEqual(response.content, body)
            self.assertEqual(response["Content-Type"], mime)
        self.publish("sitemap.xml", b"<urlset/>", "application/xml")
        self.assertEqual(self.client.get("/sitemaps/academy.xml").content, b"<urlset/>")

    def test_head_post_unknown_and_token_cache_safety(self) -> None:
        self.publish("confirm/", b"confirmation page")
        response = self.client.get("/academy/confirm/?token=opaque-test-token")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertNotIn(b"opaque-test-token", response.content)
        self.assertEqual(self.client.head("/academy/confirm/").content, b"")
        self.assertEqual(self.client.post("/academy/confirm/").status_code, 405)
        self.assertEqual(self.client.get("/academy/unknown/").status_code, 404)
        self.assertEqual(resolve("/academy/").app_name, "academy")

    def test_analytics_uses_site_consent_and_never_tracks_confirmation_tokens(self) -> None:
        from core.source_policy import umami_public_path

        for path in ("", "sessions/", "bildungsgutschein/", "faq/"):
            self.assertTrue(umami_public_path("/academy/" + path))
        self.assertFalse(umami_public_path("/academy/confirm/"))
        self.assertFalse(umami_public_path("/academy/sessions/materials/"))
        html = (
            b"<html><body><h1>Source</h1><script defer "
            b'src="https://umami.169.58.119.163.sslip.io/script.js" '
            b'data-website-id="source-id"></script></body></html>'
        )
        self.publish("", html)
        self.publish("confirm/", html)
        with self.settings(ALLOWED_HOSTS=["prod.datatalks.club"], NOINDEX=False):
            response = self.client.get("/academy/", HTTP_HOST="prod.datatalks.club")
            self.assertContains(response, 'data-umami-page-path="/academy/"')
            self.assertContains(response, "data-analytics-preferences-open")
            self.assertNotContains(response, 'data-website-id="source-id"')
            api = "https://augldvp3omxdxz3irqsmmxc25e0blpxx.lambda-url.eu-west-1.on.aws"
            connect = response["Content-Security-Policy"].split("connect-src ")[1].split(";")[0]
            self.assertIn(api + "/signup", connect.split())
            self.assertIn(api + "/confirm", connect.split())
            self.assertNotIn(api, connect.split())
            self.assertNotIn(api + "/pv", connect.split())
            response = self.client.get(
                "/academy/confirm/?token=opaque-test-token", HTTP_HOST="prod.datatalks.club"
            )
            self.assertNotContains(response, "data-umami-script-url")
            self.assertNotContains(response, "opaque-test-token")


class PublishAcademyTests(TestCase):
    def setUp(self) -> None:
        # The importer forbids repository-contained production content. Scratch
        # fixtures stay in .tmp; an overridden BASE_DIR exercises that guard.
        scratch = Path(settings.BASE_DIR) / ".tmp"
        scratch.mkdir(exist_ok=True)
        self.directory = TemporaryDirectory(dir=scratch)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "index.html").write_text("<h1>Source academy</h1>")
        (self.root / "sitemap.xml").write_text(
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<url><loc>https://datatalks.club/academy/</loc></url></urlset>"
        )

    def run_publish(self) -> None:
        with self.settings(BASE_DIR=self.root / "application"):
            call_command("publish_academy", self.root, source_commit="a" * 40, verbosity=0)

    def test_import_is_idempotent_keeps_old_assets_and_reads_database(self) -> None:
        (self.root / "old.js").write_text("old asset")
        self.run_publish()
        (self.root / "old.js").unlink()
        (self.root / "index.html").write_text("<h1>Updated source academy</h1>")
        self.run_publish()
        shutil.rmtree(self.root)
        self.assertEqual(PublishedResource.objects.count(), 3)
        self.assertContains(self.client.get("/academy/"), "Updated source academy")
        self.assertEqual(self.client.get("/academy/old.js").content, b"old asset")
        self.assertEqual(self.client.get("/academy/sitemap.xml").status_code, 200)

    def test_invalid_build_and_unpublished_sitemap_do_not_write_partial_rows(self) -> None:
        for sitemap in (
            "not xml",
            "<urlset><url><loc>https://other.test/</loc></url></urlset>",
            "<urlset><url><loc>https://datatalks.club/academy/missing/</loc></url></urlset>",
        ):
            (self.root / "sitemap.xml").write_text(sitemap)
            with self.assertRaises(CommandError):
                self.run_publish()
            self.assertFalse(PublishedResource.objects.exists())

    def test_repository_builds_and_invalid_commit_are_rejected(self) -> None:
        with self.assertRaisesMessage(CommandError, "outside the repository"):
            call_command("publish_academy", self.root, source_commit="a" * 40)
        with self.assertRaisesMessage(CommandError, "complete commit SHA"):
            call_command("publish_academy", self.root, source_commit="main")
