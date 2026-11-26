from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.template import Context, Template
from django.test import RequestFactory, SimpleTestCase, override_settings

from core.context_processors import site_context
from core.middleware import apply_security_headers
from core.source_policy import UMAMI_ORIGIN, UMAMI_WEBSITE_ID, umami_public_path


@override_settings(ALLOWED_HOSTS=["*"])
class UmamiPolicyTests(SimpleTestCase):
    def context(self, path="/", host="prod.datatalks.club", authenticated=False):
        request = RequestFactory().get(path, HTTP_HOST=host)
        request.user = AnonymousUser()
        if authenticated:
            request.user = type("SignedIn", (), {"is_authenticated": True})()
        with (
            patch("core.context_processors.public_announcement", return_value=None),
            patch("core.context_processors.public_primary_navigation", return_value=()),
        ):
            return site_context(request)

    def test_only_anonymous_public_production_pages_receive_tracker_configuration(self):
        for host in ("datatalks.club", "www.datatalks.club", "prod.datatalks.club"):
            with self.subTest(host=host):
                context = self.context(host=host)
                self.assertEqual(context["umami_script_url"], f"{UMAMI_ORIGIN}/script.js")
                self.assertEqual(context["umami_website_id"], UMAMI_WEBSITE_ID)
        for host in ("dev.datatalks.club", "localhost", "preview.datatalks.club"):
            self.assertEqual(self.context(host=host)["umami_script_url"], "")
        self.assertEqual(self.context(authenticated=True)["umami_script_url"], "")
        for path in (
            "/accounts/login/",
            "/studio/",
            "/courses/ml/2026/homework/1",
            "/events/secret/manage",
            "/?preview_token=synthetic",
            "/?TOKEN=synthetic",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.context(path=path)["umami_script_url"], "")

    def test_public_path_allowlist_rejects_identifiers_and_nested_private_routes(self):
        for path in (
            "/",
            "/privacy",
            "/blog/safe-slug.html",
            "/books/safe-slug.html",
            "/podcast/s01/e01-safe-slug/",
            "/podcast/safe-slug.html",
            "/people/safe-person.html",
            "/wiki/safe-topic",
            "/courses/safe-course",
            "/events/123/safe-event",
            "/faq/safe-course.html",
            "/books",
        ):
            self.assertTrue(umami_public_path(path), path)
        for path in (
            "/admin/",
            "/courses/example/homework/",
            "/blog/person@example.com",
            "/blog/%73ecret",
            "/internal/mail/token",
            "/register/secret",
        ):
            self.assertFalse(umami_public_path(path), path)

    def test_csp_allows_only_the_exact_required_analytics_origin(self):
        response = HttpResponse()
        apply_security_headers(response)
        directives = dict(
            part.strip().split(" ", 1)
            for part in response.headers["Content-Security-Policy"].split(";")
        )
        self.assertEqual(directives["script-src"], f"'self' 'unsafe-inline' {UMAMI_ORIGIN}")
        self.assertEqual(directives["connect-src"], f"'self' {UMAMI_ORIGIN}")
        self.assertEqual(directives["object-src"], "'none'")

    def test_template_has_inert_configuration_only_and_updated_copy(self):
        html = Template('{% include "core/_site_footer.html" %}').render(Context(self.context()))
        self.assertIn(f'data-umami-website-id="{UMAMI_WEBSITE_ID}"', html)
        self.assertNotIn(f'src="{UMAMI_ORIGIN}', html)
        self.assertIn("self-hosted Umami", html)
        self.assertNotIn("currently ships with analytics disabled", html)
