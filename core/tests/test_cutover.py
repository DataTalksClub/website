from unittest.mock import Mock, patch

from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from django.urls import resolve

from website.cutover import ApexCutoverMiddleware, _public_success


@override_settings(
    ALLOWED_HOSTS=[
        "datatalks.club",
        "www.datatalks.club",
        "prod.datatalks.club",
        "dev.datatalks.club",
    ],
    NOINDEX=True,
    APEX_CUTOVER_ENABLED=True,
)
class ApexCutoverTests(TestCase):
    def get(self, path, host="datatalks.club"):
        return self.client.get(path, HTTP_HOST=host, secure=True)

    def test_only_apex_public_success_can_be_indexed(self):
        response = self.get("/courses")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("X-Robots-Tag", response)
        for host in ("prod.datatalks.club", "dev.datatalks.club"):
            self.assertEqual(self.get("/courses", host)["X-Robots-Tag"], "noindex, nofollow")

    @override_settings(APEX_CUTOVER_ENABLED=False)
    def test_default_is_still_noindex_and_robots_disallow(self):
        self.assertEqual(self.get("/courses")["X-Robots-Tag"], "noindex, nofollow")
        self.assertContains(self.get("/robots.txt"), "Disallow: /")

    def test_robots_body_changes_on_apex_only_and_retains_security(self):
        response = self.get("/robots.txt")
        self.assertContains(response, "Sitemap: https://datatalks.club/sitemap.xml")
        self.assertNotContains(response, "Disallow: /\n")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertContains(self.get("/robots.txt", "prod.datatalks.club"), "Disallow: /")

    def test_private_errors_and_sensitive_query_keep_noindex(self):
        for path in ("/accounts/login/", "/missing-page", "/courses?token=opaque"):
            with self.subTest(path=path):
                self.assertEqual(self.get(path)["X-Robots-Tag"], "noindex, nofollow")

    @override_settings(APEX_CUTOVER_ENABLED=False)
    def test_www_redirect_is_ready_before_indexing_switch(self):
        response = self.get("/courses?source=archive", "www.datatalks.club")
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "https://datatalks.club/courses?source=archive")

    def test_www_never_replays_bodies_or_reflects_sensitive_query(self):
        response = self.client.post("/courses", {"value": "opaque"}, HTTP_HOST="www.datatalks.club")
        self.assertEqual(response.status_code, 405)
        self.assertNotIn("Location", response)
        response = self.get("/courses?token=opaque", "www.datatalks.club")
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("Location", response)

    def test_untrusted_host_cannot_activate_switch(self):
        self.assertEqual(self.get("/courses", "untrusted.invalid").status_code, 400)

    @override_settings(SECURE_HSTS_SECONDS=31536000)
    def test_www_preserves_security_and_never_calls_unsafe_view(self):
        downstream = Mock()
        middleware = ApexCutoverMiddleware(downstream)
        request = RequestFactory().post("/courses", HTTP_HOST="www.datatalks.club", secure=True)
        response = middleware(request)
        downstream.assert_not_called()
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["X-Frame-Options"], "DENY")
        self.assertIn("max-age=31536000", response["Strict-Transport-Security"])

    def test_private_or_cookie_response_never_becomes_indexable(self):
        request = RequestFactory().get("/courses", HTTP_HOST="datatalks.club")
        request.resolver_match = resolve("/courses")
        for directive in ("private", "no-store"):
            response = HttpResponse("", headers={"Cache-Control": directive})
            self.assertFalse(_public_success(request, response))
        response = HttpResponse("", headers={"Cache-Control": "public, max-age=0"})
        response.set_cookie("sessionid", "opaque")
        self.assertFalse(_public_success(request, response))

    @override_settings(ANONYMOUS_PAGE_CACHE_ENABLED=True)
    def test_cached_public_page_is_indexable_on_apex_only(self):
        """A page-cache hit skips URL resolution; the apex exception must still apply."""
        from core import page_cache

        self.assertEqual(self.get("/", "prod.datatalks.club").status_code, 200)
        real = page_cache.cached_response
        hits = []

        def recording(key):
            response = real(key)
            hits.append(response is not None)
            return response

        with patch.object(page_cache, "cached_response", recording):
            apex = self.get("/")
            prod = self.get("/", "prod.datatalks.club")
        self.assertEqual(hits, [True, True])
        self.assertEqual(apex.status_code, 200)
        self.assertNotIn("X-Robots-Tag", apex)
        self.assertEqual(prod["X-Robots-Tag"], "noindex, nofollow")
