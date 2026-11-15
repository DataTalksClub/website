from unittest import mock

import requests
from django.test import TestCase, override_settings

from core.academy_proxy import ACADEMY_API_ORIGIN, DEFAULT_ACADEMY_ORIGIN_URL


class AcademyProxyTests(TestCase):
    def test_academy_root_and_sessions_proxy_html_and_headers(self) -> None:
        upstream = mock.Mock(
            status_code=200,
            content=b"<html><body>DataTalks.Club Academy</body></html>",
            headers={
                "Content-Type": "text/html",
                "Cache-Control": "public, max-age=300",
                "ETag": '"abc123"',
                "Last-Modified": "Sun, 15 Nov 2026 15:10:56 GMT",
            },
        )
        with mock.patch("core.academy_proxy.requests.request", return_value=upstream) as req:
            response = self.client.get("/academy/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"<html><body>DataTalks.Club Academy</body></html>")
        self.assertEqual(response.headers["Content-Type"], "text/html")
        self.assertEqual(response.headers["Cache-Control"], "public, max-age=300")
        self.assertEqual(response.headers["ETag"], '"abc123"')
        self.assertEqual(response.headers["Last-Modified"], "Sun, 15 Nov 2026 15:10:56 GMT")
        csp = response.headers["Content-Security-Policy"]
        self.assertIn(ACADEMY_API_ORIGIN, csp)
        self.assertIn("script-src 'self' 'unsafe-inline'", csp)
        req.assert_called_once_with(
            "GET",
            f"{DEFAULT_ACADEMY_ORIGIN_URL}/academy/",
            allow_redirects=False,
            timeout=15,
        )

    def test_academy_astro_asset_preserves_content_type_and_cache_control(self) -> None:
        upstream = mock.Mock(
            status_code=200,
            content=b"console.log('academy');",
            headers={
                "Content-Type": "text/javascript",
                "Cache-Control": "public, max-age=31536000, immutable",
            },
        )
        with mock.patch("core.academy_proxy.requests.request", return_value=upstream) as req:
            response = self.client.get("/academy/_astro/app.1234.js")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"], "text/javascript")
        self.assertEqual(response.headers["Cache-Control"], "public, max-age=31536000, immutable")
        req.assert_called_once_with(
            "GET",
            f"{DEFAULT_ACADEMY_ORIGIN_URL}/academy/_astro/app.1234.js",
            allow_redirects=False,
            timeout=15,
        )

    @override_settings(ACADEMY_ORIGIN_URL="https://academy-origin.example.com/")
    def test_slashless_academy_preserves_redirect_and_rewrites_origin_host(self) -> None:
        upstream = mock.Mock(
            status_code=301,
            content=b"",
            headers={"Location": "https://academy-origin.example.com/academy/"},
        )
        with mock.patch("core.academy_proxy.requests.request", return_value=upstream) as req:
            response = self.client.get("/academy")

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.headers["Location"], "/academy/")
        req.assert_called_once_with(
            "GET",
            "https://academy-origin.example.com/academy",
            allow_redirects=False,
            timeout=15,
        )

    def test_head_request_returns_empty_body(self) -> None:
        upstream = mock.Mock(
            status_code=200,
            content=b"should-not-be-sent",
            headers={"Content-Type": "text/html"},
        )
        with mock.patch("core.academy_proxy.requests.request", return_value=upstream) as req:
            response = self.client.head("/academy/sessions/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"")
        req.assert_called_once_with(
            "HEAD",
            f"{DEFAULT_ACADEMY_ORIGIN_URL}/academy/sessions/",
            allow_redirects=False,
            timeout=15,
        )

    def test_post_is_rejected_with_405(self) -> None:
        with mock.patch("core.academy_proxy.requests.request") as req:
            response = self.client.post("/academy/")
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.headers["Allow"], "GET, HEAD")
        req.assert_not_called()

    def test_path_traversal_is_rejected_with_404(self) -> None:
        with mock.patch("core.academy_proxy.requests.request") as req:
            response = self.client.get("/academy/../secret")
        self.assertEqual(response.status_code, 404)
        req.assert_not_called()

    def test_upstream_error_returns_502(self) -> None:
        with mock.patch(
            "core.academy_proxy.requests.request",
            side_effect=requests.Timeout("timed out"),
        ):
            response = self.client.get("/academy/")
        self.assertEqual(response.status_code, 502)
