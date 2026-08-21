"""Browser contracts for the projection-backed episode knowledge graph (issue #217)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect

from content.public_data import public_projection
from playwright_tests.accessibility_support import assert_accessible_page

pytestmark = [pytest.mark.core]

REPRESENTATIVE = (
    "s23e06-data-engineer-career-in-2026-roles-specializations-and-what-companies-look-for"
)
SCREENSHOTS = Path(".tmp/screenshots/issue-217")


def _settle_analytics_preferences(page: Page) -> None:
    dialog = page.get_by_role("dialog", name="Optional analytics")
    if dialog.is_visible():
        dialog.get_by_role("button", name="Keep analytics off").click()


def _stub_video_provider(page: Page) -> None:
    page.route(
        "https://www.youtube-nocookie.com/**",
        lambda route: route.fulfill(status=200, content_type="text/html", body=""),
    )


def _assert_no_horizontal_overflow(page: Page) -> None:
    dimensions = page.evaluate(
        """() => ({
          viewport: document.documentElement.clientWidth,
          content: document.documentElement.scrollWidth,
        })"""
    )
    assert dimensions["content"] <= dimensions["viewport"], dimensions


def _assert_narrow_hub_clear(page: Page) -> None:
    intersections = page.locator(".episode-knowledge-graph .graph-svg-narrow").evaluate(
        """(svg) => {
          const box = (node) => {
            const rect = node.getBBox();
            return {
              left: rect.x,
              top: rect.y,
              right: rect.x + rect.width,
              bottom: rect.y + rect.height,
            };
          };
          const hub = box(svg.querySelector(".graph-svg-hub .graph-svg-shape"));
          return [...svg.querySelectorAll(".graph-svg-node:not(.graph-svg-hub) .graph-svg-shape")]
            .map((node) => ({label: node.parentElement.textContent.trim(), ...box(node)}))
            .filter((node) => (
              node.left < hub.right
              && hub.left < node.right
              && node.top < hub.bottom
              && hub.top < node.bottom
            ));
        }"""
    )
    assert intersections == [], intersections


def _screenshot(page: Page, name: str) -> None:
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOTS / name, full_page=True)


def _graph_screenshot(page: Page, name: str) -> None:
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    page.locator(".episode-knowledge-graph").screenshot(path=SCREENSHOTS / name)


def _assert_no_external_graph_request(urls: list[str]) -> None:
    forbidden_fragments = ("podwiki", "/graph/graph.json", "graph-api", "knowledge-graph")
    assert not [url for url in urls if any(fragment in url for fragment in forbidden_fragments)]


def test_episode_graph_has_complete_links_and_a_bounded_visual_on_desktop(
    page: Page,
    live_server,
) -> None:
    episode = public_projection()["podcasts_by_slug"][REPRESENTATIVE]
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))
    page.set_viewport_size({"width": 1440, "height": 900})
    _stub_video_provider(page)
    response = page.goto(f"{live_server.url}{episode['public_path']}", wait_until="networkidle")
    assert response is not None and response.status == 200
    _settle_analytics_preferences(page)

    expect(page.get_by_role("heading", name="Related knowledge graph", exact=True)).to_be_visible()
    expect(page.locator("#episode-graph-connections > li")).to_have_count(23)
    expect(page.locator("#episode-graph-connections a")).to_have_count(23)
    expect(page.locator(".episode-knowledge-graph svg[aria-hidden='true']")).to_have_count(2)
    graph_links = page.locator("#episode-graph-connections")
    expect(graph_links.get_by_role("link", name=re.compile("Slawomir Tulski"))).to_have_attribute(
        "href", "/people/slawomirtulski.html"
    )
    expect(
        graph_links.get_by_role("link", name=re.compile(r"^Data Engineering \("))
    ).to_have_attribute("href", "/wiki/data-engineering")
    expect(
        graph_links.get_by_role("link", name=re.compile(r"^Portfolio Projects \("))
    ).to_have_attribute("href", "/wiki/portfolio-projects")

    first_connection = page.locator("#episode-graph-connections a").first
    first_connection.focus()
    expect(first_connection).to_be_focused()
    _screenshot(page, "s23e06-desktop-1440x900.png")
    _graph_screenshot(page, "s23e06-desktop-1440x900-graph.png")
    _assert_no_horizontal_overflow(page)
    _assert_no_external_graph_request(requests)


@pytest.mark.accessibility
def test_episode_graph_is_accessible_on_a_mobile_viewport(
    page: Page,
    live_server,
) -> None:
    episode = public_projection()["podcasts_by_slug"][REPRESENTATIVE]
    page.set_viewport_size({"width": 390, "height": 844})
    _stub_video_provider(page)
    response = page.goto(f"{live_server.url}{episode['public_path']}", wait_until="networkidle")
    assert response is not None and response.status == 200
    _settle_analytics_preferences(page)
    expect(page.get_by_role("heading", name="Related knowledge graph", exact=True)).to_be_visible()
    expect(page.locator("#episode-graph-connections > li")).to_have_count(23)
    expect(page.locator("#episode-graph-connections a")).to_have_count(23)
    connection = page.locator("#episode-graph-connections a").first
    connection.focus()
    expect(connection).to_be_focused()
    _assert_narrow_hub_clear(page)
    _screenshot(page, "s23e06-mobile-390x844-light.png")
    _graph_screenshot(page, "s23e06-mobile-390x844-light-graph.png")
    page.locator("#dark-mode-toggle").click()
    expect(page.locator("body.dark-mode")).to_have_count(1)
    _assert_narrow_hub_clear(page)
    _screenshot(page, "s23e06-mobile-390x844-dark.png")
    _graph_screenshot(page, "s23e06-mobile-390x844-dark-graph.png")
    page.locator("#dark-mode-toggle").click()
    expect(page.locator("body.dark-mode")).to_have_count(0)
    _assert_no_horizontal_overflow(page)
    assert_accessible_page(page, "issue-217-s23e06-mobile")


def test_episode_graph_has_a_native_fallback_without_javascript(
    browser: Browser,
    live_server,
) -> None:
    episode = public_projection()["podcasts_by_slug"][REPRESENTATIVE]
    context = browser.new_context(
        java_script_enabled=False,
        viewport={"width": 390, "height": 844},
        color_scheme="light",
        reduced_motion="reduce",
        service_workers="block",
    )
    page = context.new_page()
    try:
        _stub_video_provider(page)
        response = page.goto(
            f"{live_server.url}{episode['public_path']}",
            wait_until="domcontentloaded",
        )
        assert response is not None and response.status == 200
        expect(
            page.get_by_role("heading", name="Related knowledge graph", exact=True)
        ).to_be_visible()
        expect(page.locator("#episode-graph-connections > li")).to_have_count(23)
        expect(page.locator("#episode-graph-connections a")).to_have_count(23)
        expect(page.locator("#podcast-video-player")).not_to_be_visible()
        expect(page.locator(".episode-knowledge-graph svg[aria-hidden='true']")).to_have_count(2)
        connection = page.locator("#episode-graph-connections a").first
        connection.focus()
        expect(connection).to_be_focused()
        _screenshot(page, "s23e06-mobile-390x844-no-js.png")
        _graph_screenshot(page, "s23e06-mobile-390x844-no-js-graph.png")
        _assert_no_horizontal_overflow(page)

    finally:
        context.close()
