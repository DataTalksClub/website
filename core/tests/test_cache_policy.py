"""Route-cache registry and the shareable-response boundary (PUB-02).

Spec 02: one versioned registry classifies every resolved Django view,
unlisted views are private/disabled, and only clean GET/HEAD responses of
registered public routes may keep shareable caching.  The matrix runs through
the real mounted middleware stack, not just direct helper calls.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from django.http import HttpResponse
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import get_resolver

import core.tests.seo_fixture_urls  # noqa: F401  (registers the fixture surface)
from core.cache_policy import (
    OPERATIONAL,
    PERMANENT_REDIRECT,
    PRIVATE_DYNAMIC,
    QUERY_ALLOWLISTS,
    ROUTE_CACHE_CLASSES,
    ROUTE_CACHE_POLICY_VERSION,
    SHAREABLE_CACHE_CLASSES,
    TEST_SURFACE_VIEWS,
    request_query_keys,
)
from core.middleware import ResponsePolicyMiddleware, apply_private_no_store


def _install_resolver_match(request: Any, func: Any) -> None:
    """Mount a synthetic resolver match; the attribute is Django-typed."""

    match: Any = SimpleNamespace(func=func, url_name=None)
    request.resolver_match = match


FIXTURE_URLCONF = "core.tests.seo_fixture_urls"


def cache_directives(response: Any) -> set[str]:
    return {
        directive.strip()
        for directive in response.headers.get("Cache-Control", "").split(",")
        if directive.strip()
    }


def assert_private_no_store(test: SimpleTestCase, response: Any) -> None:
    directives = cache_directives(response)
    test.assertIn("private", directives)
    test.assertIn("no-store", directives)
    test.assertNotIn("public", directives)


def _resolver_view_paths() -> set[str]:
    paths: set[str] = set()

    def walk(patterns: Any) -> None:
        for pattern in patterns:
            if hasattr(pattern, "url_patterns"):
                walk(pattern.url_patterns)
                continue
            func = pattern.callback
            view_class = getattr(func, "view_class", None)
            if view_class is not None:
                paths.add(f"{view_class.__module__}.{view_class.__name__}")
            else:
                paths.add(f"{func.__module__}.{func.__name__}")

    walk(get_resolver().url_patterns)
    return paths


class RouteRegistryCompletenessTests(SimpleTestCase):
    def test_policy_version_is_pinned(self) -> None:
        self.assertEqual(ROUTE_CACHE_POLICY_VERSION, 1)

    def test_every_resolved_view_is_classified(self) -> None:
        view_paths = _resolver_view_paths()
        unclassified = view_paths - ROUTE_CACHE_CLASSES.keys()
        self.assertEqual(unclassified, set())

    def test_registry_has_no_stale_production_entries(self) -> None:
        stale = ROUTE_CACHE_CLASSES.keys() - _resolver_view_paths()
        self.assertEqual(stale, set() | TEST_SURFACE_VIEWS)

    def test_package_studio_routes_are_private_dynamic(self) -> None:
        package_studio_routes = {
            view_path
            for view_path in _resolver_view_paths()
            if view_path.startswith("community_base.studio.")
        }
        self.assertEqual(
            package_studio_routes,
            {
                "community_base.studio.impersonation.start",
                "community_base.studio.impersonation.stop",
                "community_base.studio.user_views.note_create",
                "community_base.studio.user_views.note_delete",
                "community_base.studio.user_views.note_edit",
                "community_base.studio.user_views.user_detail",
                "community_base.studio.user_views.user_export",
                "community_base.studio.user_views.user_list",
                "community_base.studio.user_views.user_tag_add",
                "community_base.studio.user_views.user_tag_remove",
                "community_base.studio.views.dashboard",
                "community_base.studio.views.global_search",
            },
        )
        for view_path in package_studio_routes:
            with self.subTest(view_path=view_path):
                self.assertEqual(ROUTE_CACHE_CLASSES[view_path], PRIVATE_DYNAMIC)

    def test_class_and_grammar_entries_are_valid(self) -> None:
        allowed_classes = SHAREABLE_CACHE_CLASSES | {PRIVATE_DYNAMIC, OPERATIONAL}
        for view_path, cache_class in ROUTE_CACHE_CLASSES.items():
            with self.subTest(view_path=view_path):
                self.assertIn(cache_class, allowed_classes)
        for view_path, allowlist in QUERY_ALLOWLISTS.items():
            with self.subTest(view_path=view_path):
                self.assertIn(view_path, ROUTE_CACHE_CLASSES)
                self.assertIsInstance(allowlist, frozenset)
                self.assertNotEqual(
                    ROUTE_CACHE_CLASSES[view_path],
                    PERMANENT_REDIRECT,
                    "redirects are unrestricted by class, not by entry",
                )

    def test_query_keys_decode_without_raising(self) -> None:
        self.assertEqual(request_query_keys("season=2&page=3"), {"season", "page"})
        self.assertEqual(request_query_keys(""), set())
        # Raw non-ASCII bytes in the environment string must degrade to a
        # fail-closed unknown key, never an exception: a damaged key never
        # matches a reviewed selector, and a clean key stays matchable.
        self.assertEqual(request_query_keys("season=%ff"), {"season"})
        self.assertEqual(request_query_keys("seas%ffon=1"), {"seas\ufffdon"})


class ShareableResponseBoundaryTests(TestCase):
    """The veto matrix, mounted through the full middleware stack."""

    @override_settings(ROOT_URLCONF=FIXTURE_URLCONF, APPEND_SLASH=False)
    def test_clean_registered_public_response_keeps_its_directives(self) -> None:
        response = Client().get("/fixture/public-cache")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(cache_directives(response), {"public", "max-age=300"})

    @override_settings(ROOT_URLCONF=FIXTURE_URLCONF, APPEND_SLASH=False)
    def test_unregistered_query_variant_forces_private_policy(self) -> None:
        response = Client().get("/fixture/public-cache?utm_source=nl")
        self.assertEqual(response.status_code, 200)
        assert_private_no_store(self, response)

    @override_settings(ROOT_URLCONF=FIXTURE_URLCONF, APPEND_SLASH=False)
    def test_set_cookie_response_forces_private_policy(self) -> None:
        response = Client().get("/fixture/cookie")
        self.assertEqual(response.status_code, 200)
        self.assertIn("synthetic_fixture_cookie", response.cookies)
        assert_private_no_store(self, response)

    @override_settings(ROOT_URLCONF=FIXTURE_URLCONF, APPEND_SLASH=False)
    def test_vary_star_response_forces_private_policy(self) -> None:
        response = Client().get("/fixture/vary-star")
        self.assertEqual(response.status_code, 200)
        assert_private_no_store(self, response)

    @override_settings(ROOT_URLCONF=FIXTURE_URLCONF, APPEND_SLASH=False)
    def test_designated_error_statuses_forces_private_policy(self) -> None:
        for path in ("/fixture/400", "/fixture/401", "/fixture/403", "/fixture/error"):
            with self.subTest(path=path):
                response = Client(raise_request_exception=False).get(path)
                assert_private_no_store(self, response)

    @override_settings(ROOT_URLCONF=FIXTURE_URLCONF, APPEND_SLASH=False)
    def test_unsafe_method_forces_private_policy(self) -> None:
        rejected = Client().get("/fixture/method")
        self.assertEqual(rejected.status_code, 405)
        assert_private_no_store(self, rejected)
        # A plain fixture view happily accepts POST, so the method veto is
        # what strips its public directives.
        accepted = Client().post("/fixture/public-cache")
        self.assertEqual(accepted.status_code, 200)
        assert_private_no_store(self, accepted)

    @override_settings(ROOT_URLCONF=FIXTURE_URLCONF, APPEND_SLASH=False)
    def test_options_is_not_shareable(self) -> None:
        response = Client().options("/fixture/public-cache")
        assert_private_no_store(self, response)

    @override_settings(ROOT_URLCONF=FIXTURE_URLCONF, APPEND_SLASH=False)
    def test_redirect_response_stays_untouched(self) -> None:
        response = Client().get("/fixture/redirect")
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("Cache-Control", response.headers)

    @override_settings(ROOT_URLCONF=FIXTURE_URLCONF, APPEND_SLASH=False)
    def test_unregistered_route_fails_closed(self) -> None:
        response = Client().get("/private/preview/")
        assert_private_no_store(self, response)

    @override_settings(ROOT_URLCONF=FIXTURE_URLCONF, APPEND_SLASH=False)
    def test_clean_unknown_path_is_the_public_not_found_class(self) -> None:
        response = Client().get("/fixture/not-a-page")
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("no-store", cache_directives(response))

    @override_settings(ROOT_URLCONF=FIXTURE_URLCONF, APPEND_SLASH=False)
    def test_unknown_path_with_query_forces_private_policy(self) -> None:
        response = Client().get("/fixture/not-a-page?q=1")
        self.assertEqual(response.status_code, 404)
        assert_private_no_store(self, response)

    @override_settings(ROOT_URLCONF=FIXTURE_URLCONF, APPEND_SLASH=False)
    def test_unknown_path_with_credential_cookie_forces_private_policy(self) -> None:
        client = Client()
        client.cookies["sessionid"] = "opaque-session"
        response = client.get("/fixture/not-a-page")
        self.assertEqual(response.status_code, 404)
        assert_private_no_store(self, response)


class ProductionSurfaceCacheTests(TestCase):
    """The same contract against real mounted routes, not fixtures."""

    def test_clean_alias_redirect_keeps_the_reviewed_policy(self) -> None:
        response = Client().get("/events.html")
        self.assertEqual(response.status_code, 301)
        self.assertEqual(cache_directives(response), {"public", "max-age=300"})

    def test_alias_redirect_preserves_the_registered_selector_query(self) -> None:
        response = Client().get("/events.html?filter=past")
        self.assertEqual(response.status_code, 301)
        self.assertEqual(cache_directives(response), {"public", "max-age=300"})
        self.assertTrue(response.headers["Location"].endswith("/events/past"))

    def test_clean_unknown_path_stays_the_public_not_found_class(self) -> None:
        response = Client().get("/definitely-not-a-page")
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("no-store", cache_directives(response))

    def test_unknown_path_with_query_forces_private_policy(self) -> None:
        response = Client().get("/definitely-not-a-page?q=1")
        self.assertEqual(response.status_code, 404)
        assert_private_no_store(self, response)

    def test_wiki_search_is_private_whichever_route_serves_it(self) -> None:
        response = Client().get("/wiki", {"q": "synthetic term"})
        self.assertEqual(response.status_code, 200)
        assert_private_no_store(self, response)


class ResponseVetoMechanicsTests(SimpleTestCase):
    """Direct middleware mechanics that real routes cannot provoke."""

    def _response(
        self,
        request: Any,
        *,
        status: int = 200,
        headers: dict | None = None,
    ) -> HttpResponse:
        return ResponsePolicyMiddleware(
            lambda _request: HttpResponse("downstream", status=status, headers=headers or {})
        )(request)

    def test_unregistered_view_path_fails_closed(self) -> None:
        request = RequestFactory().get("/synthetic")
        _install_resolver_match(
            request,
            SimpleNamespace(__module__="some.future.module", __name__="new_view"),
        )
        response = self._response(request, headers={"Cache-Control": "public, max-age=300"})
        assert_private_no_store(self, response)

    def test_unmatched_success_response_stays_untouched(self) -> None:
        request = RequestFactory().get("/synthetic")
        response = self._response(request, headers={"Cache-Control": "public, max-age=300"})
        self.assertEqual(cache_directives(response), {"public", "max-age=300"})

    def test_status_409_and_429_are_vetoed(self) -> None:
        for status in (409, 429, 503):
            with self.subTest(status=status):
                request = RequestFactory().get("/synthetic")
                response = self._response(request, status=status)
                assert_private_no_store(self, response)

    def test_alias_redirect_on_a_hybrid_public_view_keeps_the_query_contract(self) -> None:
        # Stale-slug event aliases redirect from a registered hub/detail view;
        # the reviewed one-hop contract preserves the raw query in Location.
        request = RequestFactory().get("/events/1/stale-title?filter=past")
        _install_resolver_match(
            request,
            SimpleNamespace(__module__="content.public_views", __name__="event_detail"),
        )
        redirect = self._response(
            request, status=301, headers={"Cache-Control": "public, max-age=300"}
        )
        self.assertEqual(cache_directives(redirect), {"public", "max-age=300"})
        # The same view's render path keeps the query veto.
        render = self._response(request, headers={"Cache-Control": "public, max-age=300"})
        assert_private_no_store(self, render)

    def test_private_declaration_downstream_is_left_alone(self) -> None:
        request = RequestFactory().get("/synthetic")
        _install_resolver_match(
            request,
            SimpleNamespace(__module__="some.future.module", __name__="new_view"),
        )
        response = self._response(request, headers={"Cache-Control": "no-store"})
        self.assertEqual(cache_directives(response), {"no-store"})

    def test_apply_private_no_store_stays_idempotent_with_veto_path(self) -> None:
        request = RequestFactory().get("/synthetic?q=arbitrary")
        _install_resolver_match(
            request,
            SimpleNamespace(__module__="content.public_views", __name__="article_detail"),
        )
        response = self._response(request)
        first = response.headers["Cache-Control"]
        apply_private_no_store(response)
        self.assertEqual(response.headers["Cache-Control"], first)
