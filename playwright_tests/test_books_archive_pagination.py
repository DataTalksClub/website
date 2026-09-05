"""The Book of the Week archive as a visitor walks it (issue #174).

The archive pages through the one shared paginator (#178), so the mechanics are
already contract-tested in Django.  What only a browser can prove is the walk:
the introduction stays above every page of records, the boundary between two
pages repeats nothing, the current page is visibly selected, the adjacent
controls and the record links are real navigations without JavaScript, and the
closed ends of the archive — a page beyond the last, a malformed selector — are
the public error pages rather than a fallback.

Everything runs with JavaScript disabled and at the two widths the issue names,
plus one 320px pass over the controls alone, because the archive is a page a
reader scrolls, not a page a script assembles.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from playwright.sync_api import Browser, Page, expect

from content import catalogue
from content.pagination import PUBLIC_PAGE_SIZE

pytestmark = [pytest.mark.full, pytest.mark.django_db(transaction=True)]

SCREENSHOTS = Path(".tmp/screenshots/issue-174")
INTRO_SENTENCE = "Each week we have a book author coming to DataTalks.Club to answer your questions"


def _books() -> list[dict[str, Any]]:
    """The archive's records, read when the test runs rather than at import.

    The catalogue is database-owned, and this module is imported during
    discovery -- before ``manage.py test`` has created a test database at all.
    Reading it at import time raised out of collection, and under ``--parallel``
    that error cannot be pickled, so it took the whole run down with it.
    """

    return list(catalogue.books())


def _record_paths(page: Page) -> list[str]:
    return page.locator(".archive-title a").evaluate_all(
        "links => links.map(link => link.getAttribute('href'))"
    )


def _shot(page: Page, size: str, name: str) -> None:
    target = SCREENSHOTS / size / name
    target.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=target, full_page=True)


@pytest.mark.parametrize(
    ("viewport", "size"),
    [({"width": 1440, "height": 900}, "desktop"), ({"width": 390, "height": 844}, "mobile")],
)
def test_books_archive_walks_its_pages_and_fails_closed_without_javascript(
    browser: Browser,
    live_server,
    viewport: dict[str, int],
    size: str,
) -> None:
    books = _books()
    page_count = -(-len(books) // PUBLIC_PAGE_SIZE)
    context = browser.new_context(java_script_enabled=False, viewport=viewport)
    page = context.new_page()
    origin = live_server.url

    try:
        # -- Page one: the clean archive, introduction above the records. -----
        response = page.goto(f"{origin}/books", wait_until="domcontentloaded")
        assert response is not None and response.status == 200
        expect(page).to_have_title("Book of the Week — DataTalks.Club")
        expect(page.locator('link[rel="canonical"]')).to_have_attribute(
            "href", "https://datatalks.club/books"
        )
        expect(page.get_by_role("heading", name="Book of the Week", exact=True)).to_be_visible()
        expect(page.get_by_text(INTRO_SENTENCE)).to_be_visible()
        expect(page.get_by_role("heading", name="How it works", exact=True)).to_be_visible()
        archive_heading = page.get_by_role("heading", name="Archive", exact=True)
        expect(archive_heading).to_be_visible()
        # Above means above on the page, not earlier in the markup: the last of
        # the introduction sits higher than the Archive heading.
        lede_bottom = page.locator(".collection-lede").last.evaluate(
            "node => node.getBoundingClientRect().bottom"
        )
        assert lede_bottom < archive_heading.evaluate("node => node.getBoundingClientRect().top")
        assert _record_paths(page) == [book["public_path"] for book in books[:PUBLIC_PAGE_SIZE]]
        navigation = page.get_by_role("navigation", name="Book archive pages")
        expect(navigation).to_have_count(1)
        expect(navigation.locator("[aria-current='page']")).to_have_text("1")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        _shot(page, size, "books-page-1.png")

        # -- Page two: the next slice, visibly current, nothing repeated. -----
        response = page.goto(f"{origin}/books?page=2", wait_until="domcontentloaded")
        assert response is not None and response.status == 200
        expect(page).to_have_title("Book of the Week — Page 2 — DataTalks.Club")
        expect(page.locator('link[rel="canonical"]')).to_have_attribute(
            "href", "https://datatalks.club/books?page=2"
        )
        expect(page.get_by_role("heading", name="Book of the Week", exact=True)).to_be_visible()
        expect(page.get_by_text(INTRO_SENTENCE)).to_be_visible()
        expect(archive_heading).to_be_visible()
        second_slice = [
            book["public_path"] for book in books[PUBLIC_PAGE_SIZE : 2 * PUBLIC_PAGE_SIZE]
        ]
        assert _record_paths(page) == second_slice
        assert not set(second_slice) & {book["public_path"] for book in books[:PUBLIC_PAGE_SIZE]}
        expect(navigation.locator("[aria-current='page']")).to_have_text("2")
        _shot(page, size, "books-page-2.png")

        # -- The adjacent controls are real navigations, both directions. -----
        page.get_by_role("link", name="Previous page — page 1").click()
        expect(page).to_have_url(f"{origin}/books")
        expect(navigation.locator("[aria-current='page']")).to_have_text("1")
        page.get_by_role("link", name="Go to page 2").click()
        expect(page).to_have_url(f"{origin}/books?page=2")
        page.get_by_role("link", name="Next page — page 3").click()
        expect(page).to_have_url(f"{origin}/books?page=3")
        expect(navigation.locator("[aria-current='page']")).to_have_text("3")

        # -- A record link from page two is the canonical detail, with a way back.
        page.goto(f"{origin}/books?page=2", wait_until="domcontentloaded")
        first_on_page = page.locator(".archive-title a").first
        expect(first_on_page).to_have_attribute("href", books[PUBLIC_PAGE_SIZE]["public_path"])
        first_on_page.click()
        expect(page).to_have_url(f"{origin}{books[PUBLIC_PAGE_SIZE]['public_path']}")
        expect(page.locator('link[rel="canonical"]')).to_have_attribute(
            "href", f"https://datatalks.club{books[PUBLIC_PAGE_SIZE]['public_path']}"
        )
        expect(page.locator("#book-heading")).to_have_text(books[PUBLIC_PAGE_SIZE]["title"])
        page.get_by_role("navigation", name="Breadcrumb").get_by_role("link", name="Books").click()
        expect(page).to_have_url(f"{origin}/books")

        page.goto(f"{origin}/books?page=2", wait_until="domcontentloaded")
        last_on_page = page.locator(".archive-title a").last
        expect(last_on_page).to_have_attribute(
            "href", books[2 * PUBLIC_PAGE_SIZE - 1]["public_path"]
        )
        last_on_page.click()
        expect(page).to_have_url(f"{origin}{books[2 * PUBLIC_PAGE_SIZE - 1]['public_path']}")

        # -- The exact first-page spelling is the same page, and stays clean. --
        response = page.goto(f"{origin}/books?page=1", wait_until="domcontentloaded")
        assert response is not None and response.status == 200
        expect(page.locator('link[rel="canonical"]')).to_have_attribute(
            "href", "https://datatalks.club/books"
        )
        expect(navigation.locator("[aria-current='page']")).to_have_text("1")

        # -- The last page ends the archive; one beyond is a real miss. -------
        response = page.goto(f"{origin}/books?page={page_count}", wait_until="domcontentloaded")
        assert response is not None and response.status == 200
        expect(navigation.locator("[aria-current='page']")).to_have_text(str(page_count))
        expect(page.get_by_role("link", name="Next page")).to_have_count(0)
        beyond = page.goto(f"{origin}/books?page={page_count + 1}", wait_until="domcontentloaded")
        assert beyond is not None and beyond.status == 404
        assert "no-store" in beyond.headers["cache-control"]
        expect(page.get_by_role("heading", name="Page not found", exact=True)).to_be_visible()

        # -- Malformed selectors are a bounded, non-reflective refusal. -------
        for spelling in ("page=0", "page=02", "page=2&page=3", "page=%32"):
            refused = page.goto(f"{origin}/books?{spelling}", wait_until="domcontentloaded")
            assert refused is not None and refused.status == 400, spelling
            assert "no-store" in refused.headers["cache-control"]
            expect(page.get_by_role("heading", name="Bad request", exact=True)).to_be_visible()
            assert spelling not in page.content()

        # -- A parameter the archive does not select on rides along ignored. --
        tagged = page.goto(
            f"{origin}/books?page=2&source=newsletter", wait_until="domcontentloaded"
        )
        assert tagged is not None and tagged.status == 200
        expect(page.locator('link[rel="canonical"]')).to_have_attribute(
            "href", "https://datatalks.club/books?page=2"
        )
        expect(navigation.locator("[aria-current='page']")).to_have_text("2")
        tagged_first = page.goto(
            f"{origin}/books?utm_source=newsletter", wait_until="domcontentloaded"
        )
        assert tagged_first is not None and tagged_first.status == 200
        expect(page.locator('link[rel="canonical"]')).to_have_attribute(
            "href", "https://datatalks.club/books"
        )

        # -- At 320px the controls reflow inside the screen and keep 44px. ----
        page.set_viewport_size({"width": 320, "height": 720})
        page.goto(f"{origin}/books?page=2", wait_until="domcontentloaded")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        control = navigation.locator(".filter-pill").first
        box = control.bounding_box()
        assert box is not None and box["height"] >= 44 and box["width"] >= 44
        controls_box = navigation.locator(".pagination-pills").bounding_box()
        assert controls_box is not None and controls_box["x"] + controls_box["width"] <= 320
    finally:
        context.close()


def test_an_empty_book_projection_is_one_clear_page_without_controls(
    browser: Browser,
    live_server,
) -> None:
    """The archive with nothing in it: the introduction stays, the controls go."""

    context = browser.new_context(
        java_script_enabled=False, viewport={"width": 1440, "height": 900}
    )
    page = context.new_page()
    origin = live_server.url

    try:
        # The archive reads the database, so it is emptied the way an un-ingested
        # database is empty rather than by patching a value in.
        with mock.patch("content.catalogue.books", return_value=()):
            response = page.goto(f"{origin}/books", wait_until="domcontentloaded")
            assert response is not None and response.status == 200
            expect(page.get_by_role("heading", name="Book of the Week", exact=True)).to_be_visible()
            expect(page.get_by_text(INTRO_SENTENCE)).to_be_visible()
            expect(page.get_by_role("heading", name="How it works", exact=True)).to_be_visible()
            expect(page.get_by_role("heading", name="Archive", exact=True)).to_be_visible()
            expect(page.get_by_text("No books are available yet.")).to_be_visible()
            expect(page.get_by_role("navigation", name="Book archive pages")).to_have_count(0)
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")

            missing = page.goto(f"{origin}/books?page=2", wait_until="domcontentloaded")
            assert missing is not None and missing.status == 404
            assert "no-store" in missing.headers["cache-control"]
            expect(page.get_by_role("heading", name="Page not found", exact=True)).to_be_visible()
    finally:
        context.close()
