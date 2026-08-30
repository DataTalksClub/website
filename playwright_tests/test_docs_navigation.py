from pathlib import Path

import pytest
from playwright.sync_api import Browser, expect

from content.docs_projection import DOCS_SEARCH_URL, docs_navigation_tree

pytestmark = [pytest.mark.full, pytest.mark.django_db(transaction=True)]

SCREENSHOTS = Path(".tmp/screenshots/issue-176")


@pytest.mark.parametrize(
    ("viewport", "size"),
    [({"width": 1440, "height": 900}, "desktop"), ({"width": 390, "height": 844}, "mobile")],
)
def test_complete_docs_tree_and_detail_context_without_javascript(
    browser: Browser,
    live_server,
    viewport: dict[str, int],
    size: str,
) -> None:
    context = browser.new_context(java_script_enabled=False, viewport=viewport)
    page = context.new_page()
    origin = live_server.url
    tree = docs_navigation_tree()
    documents = tree.documents
    middle_index = len(documents) // 2
    middle = documents[middle_index]

    try:
        home_response = page.goto(f"{origin}/docs/", wait_until="domcontentloaded")
        assert home_response is not None and home_response.status == 200
        expect(
            page.get_by_role("heading", name="DataTalks.Club Zoomcamps Notes and Resources")
        ).to_be_visible()
        home_tree = page.get_by_role("navigation", name="Documentation sections")
        expect(home_tree).to_have_count(1)
        tree_links = home_tree.get_by_role("link")
        expect(tree_links).to_have_count(105)
        assert tree_links.evaluate_all("links => links.map(link => link.getAttribute('href'))") == [
            item.public_path for item in documents
        ]
        expect(
            page.get_by_role("link", name="Search documentation on GitHub (opens in a new tab)")
        ).to_have_attribute("href", DOCS_SEARCH_URL)
        expect(
            page.get_by_role("link", name="Edit this page on GitHub (opens in a new tab)")
        ).to_have_attribute("href", tree.root.page["edit_url"])
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        home_screenshot = SCREENSHOTS / size / "docs-home.png"
        home_screenshot.parent.mkdir(parents=True, exist_ok=True)
        if size == "mobile":
            page.get_by_role(
                "heading", name="DataTalks.Club Zoomcamps Notes and Resources"
            ).evaluate("element => element.scrollIntoView({block: 'start'})")
        page.screenshot(path=home_screenshot)

        detail_response = page.goto(f"{origin}{middle.public_path}", wait_until="domcontentloaded")
        assert detail_response is not None and detail_response.status == 200
        expect(page.locator('link[rel="canonical"]')).to_have_attribute(
            "href", f"https://datatalks.club{middle.public_path}"
        )
        detail_tree = page.get_by_role("navigation", name="Documentation sections")
        detail_links = detail_tree.locator("a.docs-tree-link")
        expect(detail_links).to_have_count(105)
        assert detail_links.evaluate_all(
            "links => links.map(link => link.getAttribute('href'))"
        ) == [item.public_path for item in documents]
        assert detail_tree.get_by_role("link").count() < len(documents)
        expect(detail_tree.locator('a[aria-current="page"]')).to_have_count(1)
        expect(detail_tree.locator('a[aria-current="page"]')).to_be_visible()
        expect(detail_tree.locator('a[aria-current="page"]')).to_have_attribute(
            "href", middle.public_path
        )
        expect(
            page.get_by_role("navigation", name="Breadcrumb").locator('[aria-current="page"]')
        ).to_have_count(1)
        context_navigation = page.get_by_role("navigation", name="Document context")
        parent_path = middle.page.get("parent_path") or "/docs/"
        parent_title = (
            tree.by_path[parent_path].title if parent_path != "/docs/" else "Documentation"
        )
        expect(
            context_navigation.get_by_role(
                "link",
                name=f"Parent {parent_title}",
            )
        ).to_have_attribute("href", parent_path)
        expect(
            context_navigation.get_by_role(
                "link", name=f"Previous {documents[middle_index - 1].title}"
            )
        ).to_have_attribute("href", documents[middle_index - 1].public_path)
        expect(
            context_navigation.get_by_role("link", name=f"Next {documents[middle_index + 1].title}")
        ).to_have_attribute("href", documents[middle_index + 1].public_path)
        expect(
            page.get_by_role("link", name="Search documentation on GitHub (opens in a new tab)")
        ).to_have_attribute("href", DOCS_SEARCH_URL)
        expect(
            page.get_by_role("link", name="Edit this page on GitHub (opens in a new tab)")
        ).to_have_attribute("href", middle.page["edit_url"])
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        detail_screenshot = SCREENSHOTS / size / "docs-detail.png"
        if size == "mobile":
            page.get_by_role("navigation", name="Breadcrumb").evaluate(
                "element => element.scrollIntoView({block: 'start'})"
            )
        page.screenshot(path=detail_screenshot)

        page.goto(f"{origin}{documents[0].public_path}", wait_until="domcontentloaded")
        first_context = page.get_by_role("navigation", name="Document context")
        expect(first_context.get_by_text("Previous", exact=True)).to_have_count(0)
        expect(first_context.get_by_text("Next", exact=True)).to_have_count(1)

        page.goto(f"{origin}{documents[-1].public_path}", wait_until="domcontentloaded")
        last_context = page.get_by_role("navigation", name="Document context")
        expect(last_context.get_by_text("Previous", exact=True)).to_have_count(1)
        expect(last_context.get_by_text("Next", exact=True)).to_have_count(0)
    finally:
        context.close()
