from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect

from content.docs_projection import docs_page
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
    expect(page.get_by_text("Search documentation on GitHub", exact=False)).to_have_count(0)
    expect(page.get_by_text("Edit this page on GitHub", exact=False)).to_have_count(0)


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


def _assert_current_summary_in_initial_viewport(
    page: Page,
    navigation,
    viewport: dict[str, int],
) -> None:
    summary = navigation.locator("summary[aria-current='page']")
    expect(summary).to_be_visible()
    box = summary.bounding_box()
    assert box is not None
    assert box["x"] >= 0
    assert box["x"] + box["width"] <= viewport["width"]
    assert box["y"] >= 0
    assert box["y"] + box["height"] <= viewport["height"]
    summary.click()
    expect(navigation.locator("details")).to_have_attribute("open", "")
    expect(navigation.locator('a[aria-current="page"]')).to_be_visible()
    summary.click()
    expect(navigation.locator("details")).not_to_have_attribute("open", "")


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
        expect(page.locator(".docs-course-row")).to_have_count(6)
        for title in (
            "Machine Learning Zoomcamp",
            "Data Engineering Zoomcamp",
            "MLOps Zoomcamp",
            "LLM Zoomcamp",
            "AI Dev Tools Zoomcamp",
            "Stock Market Analytics Zoomcamp",
            "Zoomcamp Logistics",
            "Course Management Platform",
            "Course FAQ",
        ):
            expect(page.get_by_role("link", name=title, exact=False).first).to_be_visible()
        # The activities row lists its pages inline, separated by a CSS ``", "`` that
        # Chrome folds into the accessible name of every link after the first, so an
        # exact accessible name matches none of them.  Name the destination instead.
        expect(page.locator('main a[href="/docs/activities/workshops/"]')).to_be_visible()
        assert page.locator('a[href^="/docs/"]').count() < 30
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
        local_navigation = page.get_by_role("navigation", name="Machine Learning Zoomcamp guide")
        expect(local_navigation.locator("a.docs-tree-link")).to_have_count(8)
        expect(local_navigation.locator('a[aria-current="page"]')).to_have_count(1)
        expect(local_navigation.locator('a[aria-current="page"]')).to_have_attribute(
            "href", "/docs/courses/ml-zoomcamp/curriculum/"
        )
        _assert_current_summary_in_initial_viewport(page, local_navigation, viewport)
        docs_main_box = page.locator(".docs-main").bounding_box()
        assert docs_main_box is not None
        if size == "desktop":
            assert docs_main_box["width"] >= 36 * 16
        expect(page.locator(".docs-curriculum-item")).to_have_count(12)
        expect(page.locator("a.docs-curriculum-card")).to_have_count(10)
        expect(page.locator(".docs-curriculum-card-static")).to_have_count(2)
        first_module = page.locator(".docs-curriculum-item").first
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
        general_navigation = page.get_by_role("navigation", name="Community Guidelines guide")
        expect(general_navigation.locator("a.docs-tree-link")).to_have_count(6)
        expect(general_navigation.locator('a[aria-current="page"]')).to_have_count(1)
        _assert_current_summary_in_initial_viewport(page, general_navigation, viewport)
        prose_box = page.locator("article.docs-body").bounding_box()
        assert prose_box is not None
        if size == "desktop":
            assert prose_box["width"] >= 36 * 16
        expect(page.locator("article.docs-body > p")).not_to_have_count(0)
        expect(page.locator("article.docs-body .docs-curriculum-item")).to_have_count(0)
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
        ("/docs/general/guidelines/ai-usage/", "using-ai-tools"),
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

    course = page.locator("a.docs-course-row").first
    assert "interactive-card" in (course.get_attribute("class") or "")
    assert "interactive-lift" in (course.get_attribute("class") or "")
    assert _interactive_state(course)["cueOpacity"] == "0"
    assert (
        course.locator(".docs-row-title").evaluate(
            "node => getComputedStyle(node).textDecorationLine"
        )
        == "none"
    )
    course.hover()
    page.wait_for_timeout(180)
    hovered_course = _interactive_state(course)
    assert hovered_course["translate"] == "-2px -2px"
    assert hovered_course["cueOpacity"] == "1"
    course.focus()
    page.wait_for_timeout(180)
    assert _interactive_state(course)["cueOpacity"] == "1"

    page.goto(
        f"{origin}/docs/courses/ml-zoomcamp/curriculum/",
        wait_until="domcontentloaded",
    )
    linked_cards = page.locator("a.docs-curriculum-card")
    expect(linked_cards).to_have_count(10)
    linked = linked_cards.first
    expect(linked).to_have_attribute("aria-label", "Module 1: Introduction")
    expect(linked).to_have_attribute(
        "href",
        "https://github.com/DataTalksClub/machine-learning-zoomcamp/tree/main/01-intro",
    )
    item_box = page.locator(".docs-curriculum-item").first.bounding_box()
    link_box = linked.bounding_box()
    assert item_box is not None and link_box is not None
    for key in ("x", "y", "width", "height"):
        assert abs(item_box[key] - link_box[key]) < 1
    assert linked.evaluate(
        """node => {
          const rect = node.getBoundingClientRect();
          const hit = document.elementFromPoint(rect.left + 6, rect.bottom - 6);
          return hit !== null && hit.closest('a') === node;
        }"""
    )
    linked.focus()
    page.wait_for_timeout(180)
    focused_card = _interactive_state(linked)
    assert focused_card["cueOpacity"] == "1"
    assert focused_card["translate"] == "-2px -2px"
    assert float(focused_card["outlineWidth"].removesuffix("px")) >= 2
    static_card = page.locator(".docs-curriculum-card-static").first
    assert _interactive_state(static_card)["cueContent"] == "none"
    context.close()

    reduced_context = browser.new_context(
        viewport={"width": 390, "height": 844},
        reduced_motion="reduce",
    )
    reduced_page = reduced_context.new_page()
    reduced_page.goto(f"{origin}/docs/", wait_until="domcontentloaded")
    reduced_course = reduced_page.locator("a.docs-course-row").first
    reduced_course.hover()
    reduced_course.focus()
    reduced_course_state = _interactive_state(reduced_course)
    assert reduced_course_state["translate"] == "none"
    assert reduced_course_state["transitionDuration"] == "0s"
    assert reduced_course_state["cueOpacity"] == "1"

    reduced_page.goto(
        f"{origin}/docs/courses/ml-zoomcamp/curriculum/",
        wait_until="domcontentloaded",
    )
    reduced_curriculum = reduced_page.locator("a.docs-curriculum-card").first
    reduced_curriculum.hover()
    reduced_curriculum.focus()
    reduced_curriculum_state = _interactive_state(reduced_curriculum)
    assert reduced_curriculum_state["translate"] == "none"
    assert reduced_curriculum_state["transitionDuration"] == "0s"
    assert reduced_curriculum_state["cueOpacity"] == "1"
    reduced_context.close()
