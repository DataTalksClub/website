from __future__ import annotations

from django.test import TestCase

ALIAS_TARGETS = (
    ("/slack.html", "/slack"),
    ("/docs", "/docs/"),
    ("/faq", "/faq/"),
    ("/blog/", "/blog"),
    ("/events/", "/events"),
)

SENSITIVE_QUERY_SPELLINGS = (
    "token=synthetic",
    "Token=synthetic",
    "PREVIEW_TOKEN=synthetic",
    "%74oken=synthetic",
    "auth=synthetic&api_key=synthetic",
    "utm_source=route%2Btest&jwt=synthetic",
)

ORDINARY_QUERY = "utm_source=route%2Btest&x=a%2Fb&blank="


def _private_cache_directives(response) -> set[str]:
    directives = {
        directive.strip().casefold()
        for directive in response.headers.get("Cache-Control", "").split(",")
        if directive.strip()
    }
    assert "no-store" in directives, directives
    assert "public" not in directives, directives
    return directives


class PublicAliasQueryPrivacyTests(TestCase):
    """A credential-shaped query must never ride a public alias redirect.

    The refusal happens before the redirect is built, so no shared cache or
    browser can store a ``Location`` carrying the credential onward, and the
    denial itself stays out of every store.
    """

    def test_credential_shaped_queries_get_a_bounded_unstored_refusal(self) -> None:
        for path, _target in ALIAS_TARGETS:
            for spelling_index, query in enumerate(SENSITIVE_QUERY_SPELLINGS):
                with self.subTest(path=path, spelling=spelling_index):
                    response = self.client.get(f"{path}?{query}")
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(response.content, b"Invalid request.")
                    self.assertNotIn("Location", response.headers)
                    _private_cache_directives(response)

    def test_head_requests_get_the_same_refusal_without_a_location(self) -> None:
        for path, _target in ALIAS_TARGETS:
            with self.subTest(path=path):
                response = self.client.head(f"{path}?token=synthetic")
                self.assertEqual(response.status_code, 400)
                self.assertNotIn("Location", response.headers)

    def test_ordinary_queries_are_still_forwarded_through_the_public_redirect(self) -> None:
        for path, target in ALIAS_TARGETS:
            with self.subTest(path=path):
                response = self.client.get(f"{path}?{ORDINARY_QUERY}")
                self.assertEqual(response.status_code, 301)
                self.assertEqual(response.headers["Location"], f"{target}?{ORDINARY_QUERY}")
                self.assertEqual(response.headers["Cache-Control"], "public, max-age=300")

    def test_unsafe_methods_keep_the_no_store_method_boundary(self) -> None:
        for path in ("/blog/", "/docs"):
            with self.subTest(path=path):
                response = self.client.post(path)
                self.assertEqual(response.status_code, 405)
                self.assertEqual(response.headers["Allow"], "GET, HEAD")
                self.assertEqual(response.headers["Cache-Control"], "no-store, max-age=0")
                self.assertEqual(response.content, b"")

    def test_event_filter_alias_refuses_the_sensitive_variant_but_keeps_the_plain_one(self) -> None:
        plain = self.client.get("/events/?filter=past")
        self.assertEqual(plain.status_code, 301)
        self.assertEqual(plain.headers["Location"], "/events/past")

        mixed = self.client.get("/events/?filter=past&auth=synthetic")
        self.assertEqual(mixed.status_code, 400)
        self.assertNotIn("Location", mixed.headers)
        _private_cache_directives(mixed)
