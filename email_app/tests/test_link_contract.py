"""The routes must be exactly the ones Relay renders into mail.

Relay builds every recipient link from `PUBLIC_BASE_URL` in
`mailing/services/public_urls.py`:

    {base}/t/o/{tracking_token}.gif
    {base}/t/c/{tracking_token}?u={destination}
    {base}/unsubscribe/{unsubscribe_token}

The three paths are what this site has to resolve. Which *host* carries them is a
deployment value, not an application one: `PUBLIC_BASE_URL` is
`https://prod.datatalks.club` while the apex still ALIASes the legacy static
site, and becomes `https://datatalks.club` after the stage-2 apex swap. The
routes must therefore be host-independent, and nothing in the application may
build one of these URLs from a request host -- a mail client fetching a pixel
presents no host worth trusting.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlencode

from django.conf import settings
from django.test import SimpleTestCase
from django.urls import resolve, reverse

TOKEN = "kD3Yy8x-Ug2f_QwErTyUiOpAsDfGhJkLzXcVbNm1234"
# Both values `PUBLIC_BASE_URL` takes across the apex swap. The routes answer to
# either, because the site never derives them from a host.
PUBLIC_BASE_URLS = ("https://prod.datatalks.club", "https://datatalks.club")


def relay_open_pixel_url(base: str, token: str) -> str:
    return f"{base}/t/o/{token}.gif"


def relay_click_redirect_url(base: str, token: str, destination: str) -> str:
    return f"{base}/t/c/{token}?{urlencode({'u': destination})}"


def relay_unsubscribe_url(base: str, token: str) -> str:
    return f"{base}/unsubscribe/{token}"


class RelayLinkShapeTests(SimpleTestCase):
    def test_relay_generated_links_resolve_to_the_bridge_views(self) -> None:
        for base in PUBLIC_BASE_URLS:
            cases = {
                relay_open_pixel_url(base, TOKEN): (
                    "relay-tracking-open",
                    {"tracking_token": TOKEN},
                ),
                relay_click_redirect_url(base, TOKEN, "https://example.com/a"): (
                    "relay-tracking-click",
                    {"tracking_token": TOKEN},
                ),
                relay_unsubscribe_url(base, TOKEN): (
                    "relay-public-unsubscribe",
                    {"unsubscribe_token": TOKEN},
                ),
            }
            for url, (expected_name, expected_kwargs) in cases.items():
                with self.subTest(base=base, url=expected_name):
                    path = url.removeprefix(base).split("?", 1)[0]
                    match = resolve(path)
                    self.assertEqual(match.url_name, expected_name)
                    self.assertEqual(match.kwargs, expected_kwargs)

    def test_the_reversed_routes_match_relays_generated_shapes(self) -> None:
        self.assertEqual(
            reverse("relay-tracking-open", kwargs={"tracking_token": TOKEN}),
            f"/t/o/{TOKEN}.gif",
        )
        self.assertEqual(
            reverse("relay-tracking-click", kwargs={"tracking_token": TOKEN}),
            f"/t/c/{TOKEN}",
        )
        self.assertEqual(
            reverse("relay-public-unsubscribe", kwargs={"unsubscribe_token": TOKEN}),
            f"/unsubscribe/{TOKEN}",
        )

    def test_the_bridge_prefixes_collide_with_no_preserved_legacy_path(self) -> None:
        """The compatibility contract must keep every one of its paths."""

        baseline = Path(settings.BASE_DIR) / "_docs/compatibility/generated-path-baseline.jsonl"
        rows = [json.loads(line) for line in baseline.read_text().splitlines() if line.strip()]
        colliding = [
            row["public_path"]
            for row in rows
            if row["public_path"] in {"/unsubscribe", "/t"}
            or row["public_path"].startswith(("/unsubscribe/", "/t/o/", "/t/c/"))
        ]
        self.assertEqual(colliding, [])


class HostIndependenceTests(SimpleTestCase):
    """The routes must work on whichever host `PUBLIC_BASE_URL` currently names."""

    def test_the_same_path_resolves_under_every_public_base_host(self) -> None:
        for base in PUBLIC_BASE_URLS:
            host = base.removeprefix("https://")
            with self.subTest(host=host):
                match = resolve(f"/unsubscribe/{TOKEN}")
                self.assertEqual(match.url_name, "relay-public-unsubscribe")

    def test_no_bridge_module_derives_an_absolute_url_from_the_request(self) -> None:
        """A mail client presents no request host worth building a URL from."""

        root = Path(settings.BASE_DIR)
        for name in ("relay_links.py", "views.py", "services.py", "urls.py", "jobs.py"):
            source = (root / "email_app" / name).read_text(encoding="utf-8")
            with self.subTest(module=name):
                self.assertNotIn("build_absolute_uri", source)
                self.assertNotIn("get_host", source)
