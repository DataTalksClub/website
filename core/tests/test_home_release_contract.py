from __future__ import annotations

from django.test import TestCase

from deploy.smoke import (
    HOME_CONTENT_PINS,
    HOME_FAILURE_TOKENS,
    HOME_IDENTITY_MARKER,
    MISSING_PAGE_FAILURE_TOKENS,
    screen_non_rendered_markup,
)

ADOPTED_COURSE_DISCOVERY_LEDE = "Learn data skills. For free. Together."


class HomeReleaseContractCoherenceTests(TestCase):
    """Keep the deploy smoke's home content pins in lockstep with the rendered homepage.

    ``run_http_smoke`` asserts these strings against the live home response during
    promotion, and the release tests exercise it with synthetic response bodies, so
    template/contract drift used to surface only as a failed deploy (issue #198).
    The pins are imported from ``deploy.smoke`` rather than re-typed: this guard
    fails Django CI whenever the rendered homepage and the release contract drift.
    """

    def test_every_home_content_pin_appears_in_the_rendered_home_response(self) -> None:
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        for pin in HOME_CONTENT_PINS:
            with self.subTest(pin=pin):
                self.assertIn(pin, html)
        # The identity marker stays asserted independently of the tuple so a future
        # tuple edit cannot silently drop it from the release contract.
        self.assertIn(HOME_IDENTITY_MARKER, html)

    def test_the_adopted_course_discovery_lede_stays_absent_from_home(self) -> None:
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(ADOPTED_COURSE_DISCOVERY_LEDE, response.content.decode("utf-8"))

    def test_rendered_home_response_is_token_free_under_the_smoke_screening(self) -> None:
        """The rendered homepage passes the deployed failure-token scan (issue #200).

        The smoke scans the screened document, so the design system's CSS
        commentary is already excluded here; a future template that puts one of
        these tokens in visible copy or in an attribute fails Django CI instead
        of a seventeen-minute deploy.
        """

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        screened = screen_non_rendered_markup(response.content.decode("utf-8")).lower()
        for token in HOME_FAILURE_TOKENS:
            with self.subTest(token=token):
                self.assertNotIn(token, screened)

    def test_rendered_missing_page_response_is_token_free_under_the_smoke_screening(self) -> None:
        """A real rendered 404 passes the missing-page leg of the same scan (#200)."""

        response = self.client.get("/__dtc_deployed_smoke_missing__")

        self.assertEqual(response.status_code, 404)
        screened = screen_non_rendered_markup(
            response.content.decode("utf-8", errors="replace")
        ).lower()
        for token in MISSING_PAGE_FAILURE_TOKENS:
            with self.subTest(token=token):
                self.assertNotIn(token, screened)
