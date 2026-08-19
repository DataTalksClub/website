from __future__ import annotations

from django.test import TestCase

from deploy.smoke import HOME_CONTENT_PINS, HOME_IDENTITY_MARKER

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
