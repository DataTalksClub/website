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
    "/podcast": (r"^/podcast/s\d+/e\d+-",),
    "/wiki": (r"^/wiki/(?!graph|special-pages|search)[^/?#]+$",),
    "/books": (r"^/books/[^/]+\.html$",),
    "/docs": (r"^/docs/.+/$",),
}
VIEWPORTS = (
    {"width": 1440, "height": 900},
    {"width": 900, "height": 900},
    # The tablet tier (36rem to 61.99rem): a half-screen window or a tablet.
    {"width": 705, "height": 900},
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


# Reading measure vs card-list measure (_docs/design/design-system.md): the blog
# article's body is the reference measure for reading text; card lists under the
# text may be as wide as the card-list column; both share the article's axis.
READING_TEXT = {
    "/wiki": ("main h1", ".wiki-prose"),
    "/podcast": (".episode-hero h1", ".episode-play", ".episode-summary", ".guest-card"),
}
CARD_LISTS = {
    "/wiki": (".wiki-episodes .event-timeline", ".related-grid"),
    "/podcast": (
        ".episode-related .event-timeline",
        ".episode-topic-grid",
        "#transcript",
        ".chapter-list",
    ),
}
CARD_LIST_MAX_PX = 848
BOX = """sel => { const e = document.querySelector(sel); if (!e) return null;
  const r = e.getBoundingClientRect(); return {left: r.left, right: r.right}; }"""


def test_reading_text_and_card_lists_hold_their_measures(page: Page, site) -> None:
    page.set_viewport_size({"width": 1366, "height": 900})
    base = site.url
    details = {path.split("/")[1]: path for path in _detail_paths(page, base)}
    assert _open(page, base + details["blog"]) == 200
    reference = page.evaluate(BOX, ".article-body")
    assert reference is not None
    axis = (reference["left"] + reference["right"]) / 2
    measure = reference["right"] - reference["left"]

    failures: list[str] = []
    for index in ("/wiki", "/podcast"):
        path = details[index.strip("/")]
        assert _open(page, base + path) == 200, path
        for selector in READING_TEXT[index]:
            box = page.evaluate(BOX, selector)
            if box is None:
                continue
            width = box["right"] - box["left"]
            if width > measure + TOLERANCE_PX:
                failures.append(
                    f"{path}: {selector} is {width}px, the reading measure is {measure}px"
                )
        for selector in CARD_LISTS[index]:
            box = page.evaluate(BOX, selector)
            if box is None:
                continue
            width = box["right"] - box["left"]
            centre = (box["left"] + box["right"]) / 2
            if width > CARD_LIST_MAX_PX + TOLERANCE_PX:
                failures.append(f"{path}: {selector} is {width}px, wider than the card-list column")
            if abs(centre - axis) > TOLERANCE_PX:
                failures.append(f"{path}: {selector} is centred at {centre}, the axis is {axis}")
    assert not failures, "\n".join(failures)
