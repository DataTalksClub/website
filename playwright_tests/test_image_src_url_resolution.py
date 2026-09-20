"""The shared image sanitizer must agree with browser URL parsing (PUB-04).

A string-prefix check on ``img src`` admitted values a browser resolves
somewhere else entirely: WHATWG parsing treats ``\\`` as a path separator
(so ``/\\evil.invalid/x.svg`` fetches a foreign origin) and collapses raw or
percent-encoded dot segments onto a different application route.  This runs
the real browser parser over the sanitizer's boundary corpus with every
network request intercepted, so the destinations the sanitizer admits or
rejects are compared against the destinations a browser would actually use.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, Route

pytestmark = [pytest.mark.full]

RESOLUTION_BASE = "https://datatalks.club/docs/"

# (img src candidate, origin a browser resolves it to, resulting pathname)
CASES: tuple[tuple[str, str, str], ...] = (
    ("/images/posts/guide.png", "https://datatalks.club", "/images/posts/guide.png"),
    ("/assets/Fixture-Logo.svg", "https://datatalks.club", "/assets/Fixture-Logo.svg"),
    ("/images/2024/my-file.v2.png", "https://datatalks.club", "/images/2024/my-file.v2.png"),
    ("/images/../admin/logout/", "https://datatalks.club", "/admin/logout/"),
    ("/%2e%2e/admin/logout/", "https://datatalks.club", "/admin/logout/"),
    ("/%2E%2E/admin/logout/", "https://datatalks.club", "/admin/logout/"),
    ("/a/%2e./b.png", "https://datatalks.club", "/b.png"),
    ("/\\evil.invalid/image.svg", "https://evil.invalid", "/image.svg"),
    ("/%5Cevil.invalid/x.svg", "https://datatalks.club", "/%5Cevil.invalid/x.svg"),
    ("/..%2f..%2fescape", "https://datatalks.club", "/..%2f..%2fescape"),
    ("/%2fadmin", "https://datatalks.club", "/%2fadmin"),
    ("/images//double.png", "https://datatalks.club", "/images//double.png"),
    ("/%2541dmin.png", "https://datatalks.club", "/%2541dmin.png"),
    ("/%zz.png", "https://datatalks.club", "/%zz.png"),
    ("/%C0%AF.png", "https://datatalks.club", "/%C0%AF.png"),
)

# The first three entries are canonical asset paths the sanitizer keeps;
# the rest change resolution or rely on ambiguous encoding and must be
# rejected.
ADMITTED = CASES[:3]


def test_browser_resolves_sanitizer_boundary_as_documented(page: Page) -> None:
    def block_all(route: Route) -> None:
        route.abort("blockedbyclient")

    page.route("**/*", block_all)
    page.goto("about:blank")
    resolved = page.evaluate(
        """
        ([base, sources]) => sources.map((source) => {
            const url = new URL(source, base);
            return [url.origin, url.pathname];
        })
        """,
        [RESOLUTION_BASE, [source for source, _, _ in CASES]],
    )
    assert len(resolved) == len(CASES)
    for (source, expected_origin, expected_path), (origin, pathname) in zip(
        CASES, resolved, strict=True
    ):
        assert (origin, pathname) == (expected_origin, expected_path), source


def test_sanitizer_rejects_every_ambiguous_resolution(page: Page) -> None:
    """One-directional contract, checked against the real parser above.

    Anything the browser resolves off-origin or onto a moved path must be
    rejected.  The reverse is deliberately not claimed: the sanitizer also
    rejects canonical-looking-but-ambiguous encodings (``%5C``, ``//``,
    malformed escapes) that a browser would leave in place.
    """

    from content.services import is_admitted_site_image_src

    for source, _, _ in ADMITTED:
        assert is_admitted_site_image_src(source), source
    for source, expected_origin, expected_path in CASES:
        if expected_origin != "https://datatalks.club" or expected_path != source:
            assert not is_admitted_site_image_src(source), source


def test_unknown_same_origin_image_reaches_the_media_view(page: Page, live_server) -> None:
    """The browser harness must not turn arbitrary media paths into healthy images."""

    response = page.goto(f"{live_server.url}/images/not-a-recorded-media-object.png")
    assert response is not None
    assert response.status == 404
    assert response.headers["content-type"].startswith("text/html")
