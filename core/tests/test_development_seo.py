from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from django.contrib.auth import get_user_model
from django.http import HttpRequest, HttpResponse
from django.template import Context, Template
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import Resolver404, resolve, reverse

from content.sitemap_contract import EXPECTED_SITEMAP_LOCATIONS, validate_sitemap_index
from core.middleware import apply_private_no_store
from core.preview import SENSITIVE_PREVIEW_QUERY_KEYS, staff_preview_required
from core.seo import validated_canonical_url
from core.views import DEVELOPMENT_ROBOTS_BODY, PRODUCTION_ROBOTS_BODY
from courses.models import Course

FIXTURE_URLCONF = "core.tests.seo_fixture_urls"
ROBOTS_VALUE = "noindex, nofollow"


def cache_directives(response: Any) -> set[str]:
    return {
        directive.strip().lower()
        for directive in response.headers.get("Cache-Control", "").split(",")
        if directive.strip()
    }


def assert_private_no_store(test: TestCase, response: Any) -> None:
    directives = cache_directives(response)
    test.assertTrue({"private", "no-store"}.issubset(directives), directives)
    test.assertNotIn("public", directives)
    test.assertFalse(
        any(item.startswith("s-maxage=") and item != "s-maxage=0" for item in directives)
    )


@override_settings(ROOT_URLCONF=FIXTURE_URLCONF, APPEND_SLASH=False, NOINDEX=True)
class DevelopmentResponsePolicyTests(TestCase):
    def test_outer_policy_overwrites_every_representative_response_class(self) -> None:
        self.client.raise_request_exception = False
        cases = (
            ("/Fixture/Unmapped.html", 200),
            ("/fixture/redirect", 302),
            ("/fixture/400", 400),
            ("/fixture/401", 401),
            ("/fixture/403", 403),
            ("/missing", 404),
            ("/fixture/method", 405),
            ("/fixture/error", 500),
            ("/fixture/json", 200),
            ("/health/live", 200),
            ("/health/ready", 200),
            ("/fixture/asset.css", 200),
            ("/robots.txt", 200),
            ("/sitemap.xml", 200),
        )
        for path, status in cases:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.headers["X-Robots-Tag"], ROBOTS_VALUE)

        conflict = self.client.get("/fixture/conflict")
        self.assertEqual(conflict.headers["X-Robots-Tag"], ROBOTS_VALUE)
        self.assertEqual(list(conflict.headers).count("X-Robots-Tag"), 1)

    def test_csrf_denial_cannot_bypass_outer_policy(self) -> None:
        response = Client(enforce_csrf_checks=True).post("/fixture/csrf")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.headers["X-Robots-Tag"], ROBOTS_VALUE)

    @override_settings(NOINDEX=False)
    def test_production_shaped_public_response_is_not_blanket_noindex(self) -> None:
        response = self.client.get("/Fixture/Unmapped.html")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("X-Robots-Tag", response.headers)

    def test_anonymous_public_response_remains_distinct_from_private_policy(self) -> None:
        response = self.client.get("/Fixture/Unmapped.html")
        self.assertNotIn("private", cache_directives(response))
        self.assertNotIn("no-store", cache_directives(response))

    def test_every_authenticated_response_is_private_even_on_public_path(self) -> None:
        user = get_user_model().objects.create_user(
            username="learner@example.test",
            email="learner@example.test",
            password="unused",
        )
        self.client.force_login(user)
        response = self.client.get("/Fixture/Unmapped.html")
        assert_private_no_store(self, response)

    def test_private_policy_removes_conflicting_shared_cache_directives(self) -> None:
        response = self.client.get("/api/conflicting-cache")
        directives = cache_directives(response)
        self.assertNotIn("public", directives)
        self.assertNotIn("s-maxage=3600", directives)
        self.assertNotIn("max-age=3600", directives)
        assert_private_no_store(self, response)

    @override_settings(
        MIDDLEWARE=[
            "core.middleware.ResponsePolicyMiddleware",
            "whitenoise.middleware.WhiteNoiseMiddleware",
        ],
        WHITENOISE_USE_FINDERS=True,
    )
    def test_whitenoise_static_short_circuit_receives_outer_policy(self) -> None:
        for headers in ({}, {"cookie": "sessionid=opaque-session"}):
            with self.subTest(headers=headers):
                response = Client().get("/static/core/site_shell.css", headers=headers)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["X-Robots-Tag"], ROBOTS_VALUE)
                self.assertIn("Content-Length", response.headers)
                if headers:
                    assert_private_no_store(self, response)

    def test_credential_bearing_early_response_fails_closed(self) -> None:
        for headers in (
            {"authorization": "Bearer opaque-input"},
            {"cookie": "sessionid=opaque-session"},
            {"cookie": "csrftoken=opaque-csrf"},
            {"cookie": "opaque_credential=opaque-value"},
            {"x-csrftoken": "opaque-csrf-header"},
            {"x-preview-token": "opaque-preview"},
            {"x-management-token": "opaque-management"},
        ):
            with self.subTest(headers=headers):
                response = self.client.get("/missing", headers=headers)
                assert_private_no_store(self, response)

    def test_private_cache_helper_is_idempotent(self) -> None:
        response = HttpResponse(headers={"Cache-Control": "no-cache, private, no-store"})
        apply_private_no_store(response)
        apply_private_no_store(response)
        self.assertEqual(
            response.headers["Cache-Control"],
            "no-cache, private, no-store, max-age=0",
        )


@override_settings(ROOT_URLCONF=FIXTURE_URLCONF, APPEND_SLASH=False, NOINDEX=True)
class DevelopmentRobotsAndSitemapTests(TestCase):
    def test_robots_get_and_head_are_exact(self) -> None:
        response = self.client.get("/robots.txt")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"], "text/plain; charset=utf-8")
        self.assertEqual(response.content.decode(), DEVELOPMENT_ROBOTS_BODY)
        self.assertNotIn("Allow", response.content.decode())
        self.assertNotIn("Sitemap", response.content.decode())

        head = self.client.head("/robots.txt")
        self.assertEqual(head.status_code, 200)
        self.assertEqual(head.headers["Content-Type"], "text/plain; charset=utf-8")
        self.assertEqual(head.content, b"")

    def test_sitemap_get_and_head_expose_the_checked_section_index(self) -> None:
        response = self.client.get("/sitemap.xml")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"], "application/xml; charset=utf-8")
        self.assertEqual(validate_sitemap_index(response.content), EXPECTED_SITEMAP_LOCATIONS)

        head = self.client.head("/sitemap.xml")
        self.assertEqual(head.status_code, 200)
        self.assertEqual(head.headers["Content-Type"], "application/xml; charset=utf-8")
        self.assertEqual(head.content, b"")

    @override_settings(NOINDEX=False)
    def test_production_robots_contract_and_public_sitemap(self) -> None:
        robots = self.client.get("/robots.txt")
        self.assertEqual(robots.status_code, 200)
        self.assertEqual(robots.content, PRODUCTION_ROBOTS_BODY.encode())
        self.assertEqual(robots.headers["Content-Type"], "text/plain; charset=utf-8")
        self.assertEqual(robots.headers["Cache-Control"], "max-age=0, must-revalidate")
        self.assertNotIn("X-Robots-Tag", robots.headers)
        self.assertNotIn("/podwiki/", robots.content.decode())
        self.assertNotIn("web.dtcdev.click", robots.content.decode())

        head = self.client.head("/robots.txt")
        self.assertEqual(head.status_code, 200)
        self.assertEqual(head.content, b"")
        self.assertEqual(head.headers["Content-Type"], robots.headers["Content-Type"])
        self.assertEqual(head.headers["Cache-Control"], robots.headers["Cache-Control"])
        self.assertNotIn("X-Robots-Tag", head.headers)

        for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
            with self.subTest(method=method):
                response = self.client.generic(method, "/robots.txt", data=b"opaque-input")
                self.assertEqual(response.status_code, 405)
                self.assertEqual(response.headers["Allow"], "GET, HEAD")
                self.assertEqual(response.headers["Cache-Control"], "no-store, max-age=0")
                self.assertNotIn("public", cache_directives(response))
                self.assertNotIn("s-maxage=3600", cache_directives(response))

        credential_responses = (
            (
                "authorization",
                self.client.get("/robots.txt", HTTP_AUTHORIZATION="Bearer opaque-input"),
                self.client.head("/robots.txt", HTTP_AUTHORIZATION="Bearer opaque-input"),
            ),
            (
                "session-cookie",
                self.client.get("/robots.txt", HTTP_COOKIE="sessionid=opaque-session"),
                self.client.head("/robots.txt", HTTP_COOKIE="sessionid=opaque-session"),
            ),
            (
                "csrf-cookie",
                self.client.get("/robots.txt", HTTP_COOKIE="csrftoken=opaque-csrf"),
                self.client.head("/robots.txt", HTTP_COOKIE="csrftoken=opaque-csrf"),
            ),
            (
                "csrf-token-header",
                self.client.get("/robots.txt", HTTP_X_CSRFTOKEN="opaque-csrf-header"),
                self.client.head("/robots.txt", HTTP_X_CSRFTOKEN="opaque-csrf-header"),
            ),
            (
                "unknown-cookie",
                self.client.get("/robots.txt", HTTP_COOKIE="opaque_credential=opaque-value"),
                self.client.head("/robots.txt", HTTP_COOKIE="opaque_credential=opaque-value"),
            ),
            (
                "preview-token-header",
                self.client.get("/robots.txt", HTTP_X_PREVIEW_TOKEN="opaque-preview"),
                self.client.head("/robots.txt", HTTP_X_PREVIEW_TOKEN="opaque-preview"),
            ),
            (
                "management-token-header",
                self.client.get("/robots.txt", HTTP_X_MANAGEMENT_TOKEN="opaque-management"),
                self.client.head("/robots.txt", HTTP_X_MANAGEMENT_TOKEN="opaque-management"),
            ),
        )
        for credential_kind, get_response, head_response in credential_responses:
            for method, response in (("GET", get_response), ("HEAD", head_response)):
                with self.subTest(credential_kind=credential_kind, method=method):
                    directives = cache_directives(response)
                    self.assertTrue({"private", "no-store", "max-age=0"}.issubset(directives))
                    self.assertNotIn("public", directives)
                    self.assertFalse(
                        any(
                            item.startswith("s-maxage=") and item != "s-maxage=0"
                            for item in directives
                        )
                    )

        for cookie in (
            "dtc_analytics_consent=v1.allow",
            "browser_timezone=Europe%2FBerlin",
        ):
            with self.subTest(cookie=cookie):
                preference = self.client.get("/robots.txt", HTTP_COOKIE=cookie)
                self.assertEqual(cache_directives(preference), {"max-age=0", "must-revalidate"})
                self.assertNotIn("private", preference.headers["Cache-Control"])

        sitemap = self.client.get("/sitemap.xml")
        self.assertEqual(sitemap.status_code, 200)
        self.assertEqual(sitemap.headers["Content-Type"], "application/xml; charset=utf-8")
        self.assertContains(sitemap, "https://datatalks.club/sitemaps/blog.xml")
        self.assertContains(sitemap, "https://datatalks.club/sitemaps/wiki.xml")
        self.assertNotIn("X-Robots-Tag", sitemap.headers)


@override_settings(ROOT_URLCONF=FIXTURE_URLCONF, APPEND_SLASH=False, NOINDEX=True)
class CanonicalPolicyTests(TestCase):
    def test_explicit_mapping_and_query_variant_share_one_canonical(self) -> None:
        for path in ("/Fixture/Exact.html", "/Fixture/Exact.html?source=fixture"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertContains(
                    response,
                    '<link rel="canonical" href="https://datatalks.club/Fixture/Exact.html">',
                    count=1,
                )

    def test_unmapped_private_error_redirect_json_and_asset_render_no_canonical(self) -> None:
        self.client.raise_request_exception = False
        for path in (
            "/Fixture/Unmapped.html",
            "/private/preview/",
            "/missing",
            "/fixture/error",
            "/fixture/redirect",
            "/fixture/json",
            "/fixture/asset.css",
            "/robots.txt",
            "/sitemap.xml",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertNotIn(b'rel="canonical"', response.content)

    def test_validator_rejects_every_non_authoritative_shape(self) -> None:
        rejected = (
            None,
            "",
            "/relative",
            "http://datatalks.club/path",
            "https://www.datatalks.club/path",
            "https://web.dtcdev.click/path",
            "https://user@datatalks.club/path",
            "https://datatalks.club:443/path",
            "https://datatalks.club/path?query=1",
            "https://datatalks.club/path#fragment",
            "https://datatalks.club//ambiguous",
            "https://datatalks.club/back\\slash",
            "https://datatalks.club/white space",
            "https://datatalks.club/control%0a",
        )
        for value in rejected:
            with self.subTest(value=value):
                self.assertEqual(validated_canonical_url(value), "")

        approved = "https://datatalks.club/Case/Exact.html"
        self.assertEqual(validated_canonical_url(approved), approved)

    def test_template_tag_escapes_and_omits_invalid_value(self) -> None:
        rendered = Template("{% load seo %}{% canonical_link value %}").render(
            Context({"value": 'https://datatalks.club/path" onload="bad'})
        )
        self.assertEqual(rendered, "")


@override_settings(ROOT_URLCONF=FIXTURE_URLCONF, APPEND_SLASH=False, NOINDEX=True)
class PreviewGuardTests(TestCase):
    def assert_preview_policy(self, response: Any) -> None:
        self.assertEqual(response.headers["X-Robots-Tag"], ROBOTS_VALUE)
        assert_private_no_store(self, response)
        self.assertNotIn(b'rel="canonical"', response.content)

    def test_anonymous_redirect_is_normal_login_with_path_only_next(self) -> None:
        response = self.client.get("/private/preview/?benign=1&next=https%3A%2F%2Fevil.example%2F")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.headers["Location"],
            "/accounts/login/?next=%2Fprivate%2Fpreview%2F",
        )
        self.assert_preview_policy(response)

    def test_active_staff_session_succeeds_without_token(self) -> None:
        staff = get_user_model().objects.create_user(
            username="staff@example.test",
            email="staff@example.test",
            is_staff=True,
            is_active=True,
        )
        self.client.force_login(staff)
        response = self.client.get("/private/preview/?benign=1")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Private staff preview")
        self.assert_preview_policy(response)

    def test_non_staff_and_inactive_principals_receive_safe_forbidden(self) -> None:
        factory = RequestFactory()

        @staff_preview_required
        def guarded(_request: HttpRequest) -> HttpResponse:
            return HttpResponse("should not render")

        for user in (
            SimpleNamespace(is_authenticated=True, is_active=True, is_staff=False),
            SimpleNamespace(is_authenticated=True, is_active=False, is_staff=True),
        ):
            with self.subTest(user=user):
                request = factory.get("/private/preview/")
                request.user = user  # type: ignore[assignment]
                response = guarded(request)
                self.assertEqual(response.status_code, 403)
                self.assertNotContains(response, "should not render", status_code=403)
                self.assert_preview_policy(response)

    def test_every_sensitive_query_key_is_rejected_case_insensitively_without_canary(self) -> None:
        canary = "preview-query-canary-36"
        for key in sorted(SENSITIVE_PREVIEW_QUERY_KEYS):
            with self.subTest(key=key):
                response = self.client.get(
                    "/private/preview/",
                    data={key.swapcase(): canary},
                )
                self.assertEqual(response.status_code, 400)
                evidence = (
                    response.content
                    + b"\n"
                    + b"\n".join(
                        f"{name}: {value}".encode() for name, value in response.headers.items()
                    )
                )
                self.assertNotIn(canary.encode(), evidence)
                self.assertNotIn("Location", response.headers)
                self.assert_preview_policy(response)

    def test_sensitive_preview_denial_log_excludes_query_and_referrer(self) -> None:
        canary = "django-request-canary-36"
        with self.assertLogs("django.request", level="WARNING") as captured:
            response = self.client.get(
                "/private/preview/",
                data={"token": canary},
                headers={"referer": f"https://external.example/?secret={canary}"},
            )

        self.assertEqual(response.status_code, 400)
        evidence = "\n".join(captured.output)
        self.assertIn("/private/preview/", evidence)
        self.assertTrue(
            any(getattr(record, "status_code", None) == 400 for record in captured.records)
        )
        self.assertNotIn(canary, evidence)
        self.assertNotIn("token=", evidence)


class RealUrlAndCourseCanonicalTests(TestCase):
    def test_preview_fixture_is_not_registered_in_real_urlconf(self) -> None:
        with self.assertRaises(Resolver404):
            resolve("/private/preview/")

    def test_course_discovery_and_detail_have_canonicals_but_learner_routes_do_not(
        self,
    ) -> None:
        hidden = Course.objects.create(
            title="Hidden course",
            slug="hidden-course",
            description="Fixture",
            visible=False,
        )
        discovery = self.client.get(reverse("course_list"))
        self.assertContains(
            discovery,
            '<link rel="canonical" href="https://datatalks.club/courses">',
            count=1,
        )

        detail_path = reverse("course", args=[hidden.slug])
        detail = self.client.get(detail_path)
        self.assertEqual(detail.status_code, 200)
        self.assertContains(
            detail,
            f'<link rel="canonical" href="https://datatalks.club{detail_path}">',
            count=1,
        )

        enrollment = self.client.get(reverse("enrollment", args=[hidden.slug]))
        self.assertEqual(enrollment.status_code, 302)
        self.assertNotIn(b'rel="canonical"', enrollment.content)

    def test_current_private_surfaces_and_learner_denial_are_private(self) -> None:
        course = Course.objects.create(
            title="Private policy course",
            slug="private-policy-course",
            description="Fixture",
        )
        paths = (
            "/studio/",
            "/studio",
            "/admin/",
            "/admin",
            "/api/v1/admin/health",
            "/api",
            "/accounts/login/",
            "/accounts",
            "/cadmin/",
            "/cadmin",
            "/auth/logout",
            reverse("enrollment", args=[course.slug]),
            "/api/courses/missing/homeworks/missing/submissions",
        )
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                assert_private_no_store(self, response)
