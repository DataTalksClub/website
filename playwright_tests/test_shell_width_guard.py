"""No public page is wider than the homepage.

The homepage's shell content box -- the same box the site footer's `.shell`
draws in on every page -- is the widest any content may sit.  Pages may be
narrower (the reading pages are), never wider: a breakout section, a wide
table or a full-bleed band's inner column that starts left of the masthead's
logo is a defect (owner rule, 2026-11-25; _docs/design/design-system.md#widths).

The probe (`shell_width_probe.js`) measures every visible text run, replaced
element and drawn box inside <main>, clipped by any scrolling ancestor.  A
full-bleed band background spanning the viewport is not content and is
skipped; its children are measured.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from playwright.sync_api import Page

pytestmark = [pytest.mark.core, pytest.mark.django_db(transaction=True)]

PROBE = (Path(__file__).with_name("shell_width_probe.js")).read_text(encoding="utf-8")
TOLERANCE_PX = 1
HIDE_CONSENT = "body dialog, body [class*=consent]{display:none!important}"

# Every public page type.  The homepage is the reference itself, so it is not
# measured against itself (its own course and story scrollers peek past the
# column by design).
PAGES = (
    "/courses",
    "/events",
    "/events/past",
    "/blog",
    "/podcast",
    "/wiki",
    "/books",
    "/docs",
    "/sponsors",
    "/slack",
    "/about",
    "/newsletter",
    "/academy/",
    "/academy/ai-engineering/",
    "/academy/bildungsgutschein/",
)
# A detail page of each kind, found from its index so the guard follows the
# reference data instead of naming one record.
DETAILS = {
    "/courses": (r"^/courses/[^/]+/?$", r"^/courses/[^/]+/\d{4}/?$"),
    "/events/past": (r"^/events/\d+/[^/]+$",),
    "/blog": (r"^/blog/[^/]+\.html$",),
    "/podcast": (r"^/podcast/s\d+e\d+/",),
    "/wiki": (r"^/wiki/(?!graph|special-pages|search)[^/?#]+$",),
    "/books": (r"^/books/[^/]+\.html$",),
    "/docs": (r"^/docs/.+/$",),
}
VIEWPORTS = (
    {"width": 1440, "height": 900},
    {"width": 900, "height": 900},
    {"width": 390, "height": 844},
)


def _open(page: Page, url: str) -> int:
    response = page.goto(url, wait_until="load")
    page.add_style_tag(content=HIDE_CONSENT)
    return response.status if response is not None else 0


def _detail_paths(page: Page, base: str) -> list[str]:
    found: list[str] = []
    for index, patterns in DETAILS.items():
        _open(page, base + index)
        hrefs = page.eval_on_selector_all(
            "main a[href]", "els => els.map(e => e.getAttribute('href') || '')"
        )
        for pattern in patterns:
            match = next((href for href in hrefs if re.match(pattern, href)), None)
            if match is not None:
                found.append(match)
    return found


@pytest.fixture
def site(live_server):
    # The Academy is not part of the shared reference data; its synthetic tree
    # is published through the real parsers, as playwright_tests/test_academy.py does.
    from academy.tests.content import publish

    publish()
    return live_server


@pytest.mark.parametrize("viewport", VIEWPORTS, ids=lambda v: str(v["width"]))
def test_no_page_content_is_wider_than_the_home_shell(
    page: Page,
    site,
    viewport: dict[str, int],
) -> None:
    page.set_viewport_size(viewport)
    base = site.url
    assert _open(page, base + "/") == 200
    home = page.evaluate(PROBE, TOLERANCE_PX)["reference"]

    paths = [*PAGES, *_detail_paths(page, base)]
    # A wide table and a chart are the elements most tempted to break out, so
    # the guard always includes at least one article and one docs page.
    assert any(path.startswith("/blog/") for path in paths), paths
    assert any(path.startswith("/docs/") for path in paths), paths

    failures: list[str] = []
    for path in paths:
        status = _open(page, base + path)
        if status != 200:
            failures.append(f"{path}: HTTP {status}")
            continue
        result = page.evaluate(PROBE, TOLERANCE_PX)
        assert result["reference"] == home, (path, result["reference"], home)
        for offender in result["offenders"][:5]:
            failures.append(
                f"{path}: {offender['path']} > {offender['element']} ({offender['kind']}) "
                f"spans {offender['left']}..{offender['right']}, home shell is "
                f"{home['left']}..{home['right']}"
            )
    assert not failures, "\n".join(failures)
