from __future__ import annotations

from django.test import RequestFactory, SimpleTestCase

from core.sensitive_query import SENSITIVE_QUERY_KEYS, has_sensitive_query_key


class SensitiveQueryClassifierTests(SimpleTestCase):
    def test_decoded_key_spellings_are_classified(self) -> None:
        cases = {
            "": False,
            "utm_source=route%2Btest&utm_campaign=launch": False,
            "q=A+B&page=2": False,
            "token=synthetic": True,
            "token=": True,
            "Token=synthetic": True,
            "PREVIEW_TOKEN=synthetic": True,
            "%74oken=synthetic": True,
            "token=one&token=two": True,
            "utm_source=nl&signature=synthetic": True,
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                request = RequestFactory().get(f"/?{query}")
                self.assertIs(has_sensitive_query_key(request), expected)

    def test_every_listed_key_is_classified_in_any_case(self) -> None:
        for key in sorted(SENSITIVE_QUERY_KEYS):
            with self.subTest(key=key):
                request = RequestFactory().get("/", data={key.swapcase(): "synthetic"})
                self.assertIs(has_sensitive_query_key(request), True)

    def test_raw_non_ascii_query_never_raises_and_is_still_classified(self) -> None:
        # A raw non-ASCII byte in the environ makes `request.GET` itself
        # raise; the classifier must answer for such a query instead.
        plain = RequestFactory().get("/?season=\u0662")
        self.assertIs(has_sensitive_query_key(plain), False)

        mixed = RequestFactory().get("/?%74oken=synthetic&season=\u0662")
        self.assertIs(has_sensitive_query_key(mixed), True)
