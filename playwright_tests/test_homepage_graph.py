"""Clicking homepage graph nodes explores in place instead of leaving the page."""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

pytestmark = [pytest.mark.core]

SCREENSHOTS = Path(".tmp/screenshots/homepage-graph")


def _settle_analytics_preferences(page: Page) -> None:
    dialog = page.get_by_role("dialog", name="Optional analytics")
    if dialog.is_visible():
        dialog.get_by_role("button", name="Keep analytics off").click()


def _shot(page: Page, name: str) -> None:
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    page.locator("[data-home-graph]").screenshot(path=SCREENSHOTS / name)


def _visible_drawing(page: Page):
    explorer = page.locator("[data-home-graph]")
    wide = explorer.locator("[data-home-graph-live] .graph-svg-wide")
    if wide.is_visible():
        return wide
    return explorer.locator("[data-home-graph-live] .graph-svg-narrow")


@pytest.mark.parametrize(
    ("viewport", "suffix"),
    [({"width": 1280, "height": 800}, "desktop"), ({"width": 390, "height": 844}, "mobile")],
)
def test_homepage_graph_explores_a_clicked_node_without_leaving(
    page: Page,
    live_server,
    viewport: dict[str, int],
    suffix: str,
) -> None:
    page.set_viewport_size(viewport)
    response = page.goto(live_server.url, wait_until="networkidle")
    assert response is not None and response.status == 200
    _settle_analytics_preferences(page)

    explorer = page.locator("[data-home-graph]")
    expect(explorer).to_have_class("graph-frame home-graph-explorer is-ready")
    drawing = _visible_drawing(page)
    hub = drawing.locator(".graph-svg-hub")
    expect(hub).to_have_attribute("data-node-id", "wiki:mlops")

    spoke = drawing.locator(".graph-svg-node:not(.graph-svg-hub)").first
    spoke_id = spoke.get_attribute("data-node-id")
    assert spoke_id
    spoke.click()

    expect(page).to_have_url(f"{live_server.url}/")
    expect(hub).to_have_attribute("data-node-id", spoke_id)
    expect(explorer.locator("[data-home-graph-status]")).to_contain_text("Exploring")
    expect(explorer.locator("[data-home-graph-back]")).to_be_visible()
    _shot(page, f"explored-{suffix}.png")

    explorer.locator("[data-home-graph-back]").click()
    expect(hub).to_have_attribute("data-node-id", "wiki:mlops")
    expect(page).to_have_url(f"{live_server.url}/")
    _shot(page, f"start-{suffix}.png")


def test_homepage_graph_open_page_is_a_separate_action(page: Page, live_server) -> None:
    page.set_viewport_size({"width": 1280, "height": 800})
    response = page.goto(live_server.url, wait_until="networkidle")
    assert response is not None and response.status == 200
    _settle_analytics_preferences(page)

    explorer = page.locator("[data-home-graph]")
    expect(explorer).to_have_class("graph-frame home-graph-explorer is-ready")
    drawing = _visible_drawing(page)
    spoke = drawing.locator(".graph-svg-node:not(.graph-svg-hub)").first
    spoke_href = spoke.get_attribute("href")
    spoke.click()
    expect(page).to_have_url(f"{live_server.url}/")

    open_page = explorer.locator("[data-home-graph-open]")
    expect(open_page).to_have_attribute("href", spoke_href or "")
    open_page.click()
    expect(page).to_have_url(f"{live_server.url}{spoke_href}")
