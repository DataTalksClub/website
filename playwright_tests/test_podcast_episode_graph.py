"""Browser contracts for an episode's knowledge-graph topics (issue #217).

The graph node's neighbours reach the page in reader terms: the wiki topics as
topic cards (folded past eight behind "Show all"), never the guests, the
graph's relation kinds or its weights.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, Page, expect

from content import catalogue
from playwright_tests.accessibility_support import assert_accessible_page

pytestmark = [pytest.mark.core]

REPRESENTATIVE = "synthetic-episode-one"
SCREENSHOTS = Path(".tmp/screenshots/issue-217")


def _episode(slug: str) -> dict[str, Any]:
    """The published episode a test names, which the catalogue must hold."""

    record = catalogue.podcast(slug)
    assert record is not None, slug
    return record


def _graph_node(node_id: str, title: str, node_type: str, url: str) -> dict[str, str]:
    return {
        "id": node_id,
        "label": title,
        "title": title,
        "type": node_type,
        "url": url,
    }


def _synthetic_episode_graph(episode: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """A deterministic full neighbourhood owned by this browser contract."""

    neighbours = [
        _graph_node("wiki:mlops", "MLOps", "wiki", "/wiki/mlops"),
        _graph_node(
            "person:synthetic-rich-profile",
            "Synthetic Rich Profile",
            "person",
            "/people/synthetic-rich-profile.html",
        ),
        _graph_node(
            "wiki:feature-stores",
            "Feature Stores",
            "wiki",
            "/wiki/feature-stores",
        ),
        *(
            _graph_node(
                f"wiki:synthetic-connection-{index:02d}",
                f"Synthetic Connection {index:02d}",
                "wiki",
                f"/wiki/synthetic-wiki-bulk-{index:04d}",
            )
            for index in range(1, 21)
        ),
    ]
    hub_id = f"podcast:{episode['slug']}"
    return {
        "nodes": [
            _graph_node(hub_id, episode["title"], "podcast", episode["public_path"]),
            *neighbours,
        ],
        "links": [
            {
                "kind": "related",
                "source": hub_id,
                "target": neighbour["id"],
                "weight": len(neighbours) - index,
            }
            for index, neighbour in enumerate(neighbours)
        ],
    }


@pytest.fixture
def graph_episode(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    episode = _episode(REPRESENTATIVE)
    graph = _synthetic_episode_graph(episode)
    monkeypatch.setattr(catalogue, "wiki_graph", lambda: graph)
    return episode


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


def _screenshot(page: Page, name: str) -> None:
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    page.locator(".episode-topics").screenshot(path=SCREENSHOTS / name)


def _assert_no_external_graph_request(urls: list[str]) -> None:
    forbidden_fragments = ("podwiki", "/graph/graph.json", "graph-api", "knowledge-graph")
    assert not [url for url in urls if any(fragment in url for fragment in forbidden_fragments)]


#: The synthetic graph's wiki neighbours; the one person is the guest.
TOPICS = 22


def _assert_reader_terms_only(page: Page) -> None:
    topics = page.locator(".episode-topics")
    expect(topics.get_by_text("Synthetic Rich Profile")).to_have_count(0)
    expect(topics.get_by_text("weight")).to_have_count(0)
    expect(topics.get_by_text("related")).to_have_count(0)
    expect(topics.get_by_role("link", name="MLOps", exact=True)).to_have_attribute(
        "href", "/wiki/mlops"
    )


def test_episode_topics_fold_past_eight_on_desktop(
    page: Page,
    live_server,
    graph_episode: dict[str, Any],
) -> None:
    episode = graph_episode
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))
    page.set_viewport_size({"width": 1440, "height": 900})
    _stub_video_provider(page)
    response = page.goto(f"{live_server.url}{episode['public_path']}", wait_until="networkidle")
    assert response is not None and response.status == 200
    _settle_analytics_preferences(page)

    expect(page.get_by_role("heading", name="Topics in this episode", exact=True)).to_be_visible()
    expect(page.get_by_text("All related connections")).to_have_count(0)
    cards = page.locator(".episode-topic-card")
    expect(cards).to_have_count(TOPICS)
    expect(page.locator(".episode-topic-card:visible")).to_have_count(8)
    _assert_reader_terms_only(page)
    _screenshot(page, "synthetic-episode-desktop-1440x900-topics.png")
    show_all = page.get_by_role("button", name=f"Show all {TOPICS} topics")
    show_all.click()
    expect(page.locator(".episode-topic-card:visible")).to_have_count(TOPICS)
    expect(show_all).to_have_count(0)
    _assert_no_horizontal_overflow(page)
    _assert_no_external_graph_request(requests)


@pytest.mark.accessibility
def test_episode_topics_are_accessible_on_a_mobile_viewport(
    page: Page,
    live_server,
    graph_episode: dict[str, Any],
) -> None:
    episode = graph_episode
    page.set_viewport_size({"width": 390, "height": 844})
    _stub_video_provider(page)
    response = page.goto(f"{live_server.url}{episode['public_path']}", wait_until="networkidle")
    assert response is not None and response.status == 200
    _settle_analytics_preferences(page)
    expect(page.get_by_role("heading", name="Topics in this episode", exact=True)).to_be_visible()
    first = page.locator(".episode-topic-card a").first
    first.focus()
    expect(first).to_be_focused()
    _screenshot(page, "synthetic-episode-mobile-390x844-light-topics.png")
    page.locator("#dark-mode-toggle").click()
    expect(page.locator("body.dark-mode")).to_have_count(1)
    _screenshot(page, "synthetic-episode-mobile-390x844-dark-topics.png")
    page.locator("#dark-mode-toggle").click()
    expect(page.locator("body.dark-mode")).to_have_count(0)
    _assert_no_horizontal_overflow(page)
    assert_accessible_page(page, "issue-217-s23e06-mobile")


def test_episode_topics_all_show_without_javascript(
    browser: Browser,
    live_server,
    graph_episode: dict[str, Any],
) -> None:
    episode = graph_episode
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
            page.get_by_role("heading", name="Topics in this episode", exact=True)
        ).to_be_visible()
        expect(page.locator(".episode-topic-card:visible")).to_have_count(TOPICS)
        expect(page.locator("[data-episode-topics-more]")).to_be_hidden()
        _assert_reader_terms_only(page)
        _screenshot(page, "synthetic-episode-mobile-390x844-no-js-topics.png")
        _assert_no_horizontal_overflow(page)
    finally:
        context.close()
