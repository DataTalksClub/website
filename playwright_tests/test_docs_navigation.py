from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect

from content.docs_presentation import (
    docs_body_without_primary_heading,
    docs_curriculum,
    docs_hub,
    docs_rail,
)
from content.docs_reader import docs_navigation_tree, docs_page
from playwright_tests.accessibility_support import assert_accessible_page

pytestmark = [pytest.mark.full, pytest.mark.django_db(transaction=True)]

SCREENSHOTS = Path(".tmp/screenshots/docs-adversarial-loop")
VIEWPORTS = (
    ({"width": 1440, "height": 900}, "desktop"),
    ({"width": 390, "height": 844}, "mobile"),
)


def _assert_no_page_overflow(page: Page) -> None:
    dimensions = page.evaluate(
        """() => ({
          scrollWidth: document.documentElement.scrollWidth,
          clientWidth: document.documentElement.clientWidth,
        })"""
    )
    assert dimensions["scrollWidth"] == dimensions["clientWidth"], dimensions


def _assert_repository_chrome_absent(page: Page) -> None:
    # Repository *utilities* stay off a public page.  "Edit this page on GitHub"
    # is not one: it is the reader's way to correct the page they are reading,
    # and the only repository link a documentation page offers.
    expect(page.get_by_text("Search documentation on GitHub", exact=False)).to_have_count(0)


def _capture(page: Page, name: str, size: str, theme: str) -> None:
    path = SCREENSHOTS / theme / size / f"{name}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    page.evaluate(
        """() => {
          document.documentElement.style.setProperty('scroll-behavior', 'auto', 'important');
          document.body.style.setProperty('scroll-behavior', 'auto', 'important');
          window.scrollTo({ top: 0, left: 0, behavior: 'instant' });
        }"""
    )
    page.wait_for_function("window.scrollY === 0 && window.scrollX === 0")
    assert page.evaluate("window.scrollY") == 0
    masthead = page.locator(".masthead")
    expect(masthead).to_be_visible()
    expect(masthead).to_be_in_viewport()
    page.screenshot(path=path)


def curriculum_items() -> tuple:
    """The modules the source curriculum actually holds, read from the page itself."""

    page = docs_page("/docs/courses/ml-zoomcamp/curriculum/")
    assert page is not None
    _heading_id, body = docs_body_without_primary_heading(str(page["body_html"]))
    curriculum = docs_curriculum(body)
    assert curriculum is not None
    return curriculum.items


def _dismiss_analytics_preferences(page: Page) -> None:
    close = page.get_by_role("button", name="Close without changing")
    if close.is_visible():
        close.click()


def _interactive_state(locator) -> dict[str, str]:
    return locator.evaluate(
        """node => {
          const style = getComputedStyle(node);
          const cue = getComputedStyle(node, '::before');
          return {
            translate: style.translate,
            transitionDuration: style.transitionDuration,
            cueContent: cue.content,
            cueOpacity: cue.opacity,
            outlineWidth: style.outlineWidth,
          };
        }"""
    )


def _assert_rail_shows_the_guide_without_a_click(page: Page, viewport: dict[str, int]) -> None:
    """The guide is visible on arrival, not behind a disclosure.

    The rail used to be a closed `<details>` whose only visible content was the
    page title again; the sibling pages were behind a "+".
    """

    rail = page.locator("#docs-rail")
    expect(rail).to_be_visible()
    expect(page.locator("details.docs-local-disclosure")).to_have_count(0)
    current = rail.locator('a[aria-current="page"]')
    expect(current).to_have_count(1)
    expect(current).to_be_visible()
    box = current.bounding_box()
    assert box is not None
    assert box["x"] >= 0
    assert box["x"] + box["width"] <= viewport["width"]


@pytest.mark.parametrize(("viewport", "size"), VIEWPORTS)
def test_docs_system_hierarchy_and_responsive_evidence(
    browser: Browser,
    live_server,
    viewport: dict[str, int],
    size: str,
) -> None:
    context = browser.new_context(viewport=viewport, reduced_motion="reduce")
    page = context.new_page()
    origin = live_server.url

    try:
        response = page.goto(f"{origin}/docs/", wait_until="domcontentloaded")
        assert response is not None and response.status == 200
        _dismiss_analytics_preferences(page)
        expect(
            page.get_by_role("heading", name="DataTalks.Club Zoomcamps Notes and Resources")
        ).to_be_visible()
        expect(page.locator("main h1")).to_have_count(1)
        hub = docs_hub(docs_navigation_tree())
        expect(page.locator(".docs-course-card")).to_have_count(len(hub.families))
        # Every family card carries its own course drawing; the hub used to be
        # the one index page on the site with no illustration at all.
        expect(page.locator(".docs-course-figure .doodle-light")).to_have_count(len(hub.families))
        for title in (
            *(guide.title for guide in hub.families),
            *(guide.title for guide in hub.sectioned),
            *(guide.title for guide in hub.platform),
        ):
            expect(page.get_by_role("link", name=title, exact=False).first).to_be_visible()
        # The activities row lists its pages inline, separated by a CSS ``", "`` that
        # Chrome folds into the accessible name of every link after the first, so an
        # exact accessible name matches none of them.  Name the destination instead.
        expect(page.locator('main a[href="/docs/activities/workshops/"]')).to_be_visible()
        # The hub reached 21 of its 105 pages before; the pages readers come for
        # -- certification, joining a cohort, the final project -- were three
        # clicks down.  Nearly the whole corpus is now one click, and the only
        # pages it may leave out are the area indexes whose whole content is the
        # list the hub itself now draws.
        tree = docs_navigation_tree()
        linked = set(
            page.eval_on_selector_all(
                'main a[href^="/docs/"]', "nodes => nodes.map(node => node.getAttribute('href'))"
            )
        )
        unreachable = [
            item.public_path for item in tree.documents if item.public_path not in linked
        ]
        areas = {item.public_path for item in tree.root.children}
        assert [path for path in unreachable if path not in areas] == []
        _assert_repository_chrome_absent(page)
        _assert_no_page_overflow(page)
        _capture(page, "docs-home", size, "light")

        response = page.goto(
            f"{origin}/docs/courses/ml-zoomcamp/curriculum/",
            wait_until="domcontentloaded",
        )
        assert response is not None and response.status == 200
        expect(page.locator("main h1")).to_have_count(1)
        expect(page.get_by_role("heading", name="Curriculum", exact=True)).to_be_visible()
        rail = page.locator("#docs-rail")
        guide_rail = docs_rail(docs_navigation_tree(), "/docs/courses/ml-zoomcamp/curriculum/")
        assert guide_rail is not None
        expect(rail.locator("a.rail-unit-link")).to_have_count(len(guide_rail.units))
        expect(rail.locator('a[aria-current="page"]')).to_have_attribute(
            "href", "/docs/courses/ml-zoomcamp/curriculum/"
        )
        _assert_rail_shows_the_guide_without_a_click(page, viewport)
        docs_main_box = page.locator(".docs-main").bounding_box()
        assert docs_main_box is not None
        if size == "desktop":
            assert docs_main_box["width"] >= 36 * 16
        expect(page.locator(".docs-curriculum-row")).to_have_count(len(curriculum_items()))
        # The row is not an anchor, so a module whose bullets hold a link can no
        # longer nest one anchor inside another.
        assert page.evaluate("document.querySelectorAll('a a').length") == 0
        first_module = page.locator(".docs-curriculum-row").first
        expect(first_module).to_contain_text("Module 1: Introduction")
        expect(first_module.get_by_role("link")).to_have_attribute(
            "href",
            "https://github.com/DataTalksClub/machine-learning-zoomcamp/tree/main/01-intro",
        )
        expect(page.locator("#learning-philosophy")).to_have_count(1)
        expect(page.locator("#pace")).to_have_count(1)
        expect(page.locator("#cohort-changes")).to_have_count(1)
        first_module_box = first_module.bounding_box()
        assert first_module_box is not None and first_module_box["y"] < viewport["height"]
        _assert_repository_chrome_absent(page)
        _assert_no_page_overflow(page)
        _capture(page, "ml-curriculum", size, "light")

        response = page.goto(
            f"{origin}/docs/general/guidelines/ai-usage/",
            wait_until="domcontentloaded",
        )
        assert response is not None and response.status == 200
        expect(page.locator("main h1")).to_have_count(1)
        rail = page.locator("#docs-rail")
        general_rail = docs_rail(docs_navigation_tree(), "/docs/general/guidelines/ai-usage/")
        assert general_rail is not None
        expect(rail.locator("a.rail-unit-link")).to_have_count(len(general_rail.units))
        _assert_rail_shows_the_guide_without_a_click(page, viewport)
        # The edit link the page data always carried, drawn at last.
        expect(page.get_by_role("link", name="Edit this page on GitHub")).to_have_count(1)
        prose_box = page.locator("article.docs-body").bounding_box()
        assert prose_box is not None
        if size == "desktop":
            assert prose_box["width"] >= 36 * 16
        expect(page.locator("article.docs-body > p")).not_to_have_count(0)
        expect(page.locator("article.docs-body .docs-curriculum-row")).to_have_count(0)
        _assert_repository_chrome_absent(page)
        _assert_no_page_overflow(page)
        _capture(page, "ai-usage", size, "light")

        assert_accessible_page(page, f"public.docs-ai-usage-{size}")

        page.goto(f"{origin}/docs/", wait_until="domcontentloaded")
        page.locator("#dark-mode-toggle").click()
        expect(page.locator("body")).to_have_attribute("data-dark-mode", "true")
        _assert_no_page_overflow(page)
        _capture(page, "docs-home", size, "dark")

        page.goto(
            f"{origin}/docs/courses/ml-zoomcamp/curriculum/",
            wait_until="domcontentloaded",
        )
        expect(page.locator("body")).to_have_attribute("data-dark-mode", "true")
        _assert_no_page_overflow(page)
        _capture(page, "ml-curriculum", size, "dark")
    finally:
        context.close()


@pytest.mark.parametrize(
    ("path", "heading_id"),
    (
        ("/docs/", "datatalks-club-zoomcamps-notes-and-resources"),
        ("/docs/courses/ml-zoomcamp/curriculum/", "curriculum"),
        ("/docs/general/guidelines/ai-usage/", "ai-usage"),
    ),
)
def test_docs_routes_and_source_anchors_remain_exact(
    page: Page,
    live_server,
    path: str,
    heading_id: str,
) -> None:
    projected = docs_page(path)
    assert projected is not None
    response = page.goto(f"{live_server.url}{path}", wait_until="domcontentloaded")
    assert response is not None and response.status == 200
    expect(page.locator('link[rel="canonical"]')).to_have_attribute(
        "href", f"https://datatalks.club{path}"
    )
    expect(page.locator("main h1")).to_have_count(1)
    expect(page.locator("main h1")).to_have_attribute("id", heading_id)


def test_docs_alias_remains_one_hop(page: Page, live_server) -> None:
    alias = page.request.get(f"{live_server.url}/docs?source=test", max_redirects=0)
    assert alias.status == 301
    assert alias.headers["location"] == "/docs/?source=test"


def test_docs_cards_use_whole_surface_interactions_and_reduced_motion(
    browser: Browser,
    live_server,
) -> None:
    origin = live_server.url
    context = browser.new_context(
        viewport={"width": 1440, "height": 900},
        has_touch=False,
        reduced_motion="no-preference",
    )
    page = context.new_page()
    page.goto(f"{origin}/docs/", wait_until="domcontentloaded")
    _dismiss_analytics_preferences(page)

    # The course card is the catalogue's own whole-card destination: one real
    # anchor, its hit area stretched over the surface, with the card's page
    # links kept above that overlay as their own targets.
    course = page.locator(".docs-course-card").first
    assert "interactive-card" in (course.get_attribute("class") or "")
    assert "interactive-lift" in (course.get_attribute("class") or "")
    assert "stretched-card-link" in (course.get_attribute("class") or "")
    assert _interactive_state(course)["cueOpacity"] == "0"
    overlay = course.locator("h3 a.course-link").evaluate(
        "el => getComputedStyle(el, '::after').position"
    )
    assert overlay == "absolute"
    page_link = course.locator(".docs-pages a").first
    page_link.scroll_into_view_if_needed()
    assert page_link.evaluate("el => getComputedStyle(el).zIndex") not in ("auto", "0")
    assert page_link.evaluate(
        """node => {
          const rect = node.getBoundingClientRect();
          const hit = document.elementFromPoint(rect.left + 4, rect.top + rect.height / 2);
          return hit !== null && hit.closest('a') === node;
        }"""
    )
    course.hover()
    page.wait_for_timeout(180)
    hovered_course = _interactive_state(course)
    assert hovered_course["translate"] == "-2px -2px"
    assert hovered_course["cueOpacity"] == "1"

    page.goto(
        f"{origin}/docs/courses/ml-zoomcamp/curriculum/",
        wait_until="domcontentloaded",
    )
    # The module row is not a card and not an anchor: the title is the link, and
    # the row draws no surface of its own.
    first_row = page.locator(".docs-curriculum-row").first
    expect(first_row).to_have_count(1)
    assert first_row.evaluate("el => el.tagName") == "LI"
    assert _interactive_state(first_row)["cueContent"] == "none"
    module_link = first_row.locator(".docs-curriculum-title a")
    expect(module_link).to_have_attribute(
        "href",
        "https://github.com/DataTalksClub/machine-learning-zoomcamp/tree/main/01-intro",
    )
    context.close()

    reduced_context = browser.new_context(
        viewport={"width": 390, "height": 844},
        reduced_motion="reduce",
    )
    reduced_page = reduced_context.new_page()
    reduced_page.goto(f"{origin}/docs/", wait_until="domcontentloaded")
    reduced_course = reduced_page.locator(".docs-course-card").first
    reduced_course.hover()
    reduced_course_state = _interactive_state(reduced_course)
    assert reduced_course_state["translate"] == "none"
    assert reduced_course_state["transitionDuration"] == "0s"
    assert reduced_course_state["cueOpacity"] == "1"
    reduced_context.close()
