"""UX-09 acceptance: enrollment sorting is global, URL-owned, and JS-free.

The Studio enrollment table's header controls are server-driven GET links:
the globally highest scorer reaches page one under "score descending" even
though the legacy ordering puts them on a later page, the sort rides through
pagination, and everything works with JavaScript disabled — the old
client-side sorter could only re-order the rows one page happened to contain.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client
from playwright.sync_api import Browser, Locator, Page, expect

from accounts.studio_test_support import make_studio_user
from courses.models import Cohort, Enrollment, User

pytestmark = [pytest.mark.full, pytest.mark.django_db(transaction=True)]

EVIDENCE = Path(".tmp/enrollment-sorting")
PAGE_SIZE = 25
ROW_COUNT = 30
TOP_SCORER = "page-student-28"
# A load can land in the tail of the previous test's database activity and
# come back as a transient 500; every navigation verifies the table actually
# rendered and gets one retry cycle of slack.
LOAD_SLACK = 10_000


@pytest.fixture
def sort_course() -> Cohort:
    course = Cohort.objects.create(
        slug="sorting-acceptance",
        title="Sorting Acceptance",
        description="UX-09 browser acceptance course",
    )
    for index in range(1, ROW_COUNT + 1):
        student = User.objects.create_user(
            username=f"page-student-{index:02d}",
            email=f"page-student-{index:02d}@example.com",
            password="test",
        )
        Enrollment.objects.create(
            student=student,
            course=course,
            position_on_leaderboard=index,
            # Strictly decreasing with position, except one clear global
            # top scorer ranked 28th — a row the legacy ordering buries on
            # page two.
            total_score=100 - index if index != 28 else 500,
        )
    return course


def enrollment_list_url(live_server, course: Cohort) -> str:
    return f"{live_server.url}/studio/courses/{course.slug}/enrollments/"


def goto_with_retry(page: Page, url: str) -> None:
    response = None
    for attempt in range(3):
        response = page.goto(url, wait_until="domcontentloaded")
        if response is not None and response.status == 200:
            return
        page.wait_for_timeout(300 * (attempt + 1))
    assert response is not None and response.status == 200, f"page never loaded: {url}"


def rows_of(page: Page) -> Locator:
    return page.locator("#enrollmentsTable tbody tr")


def first_row(page: Page) -> Locator:
    return page.locator("#enrollmentsTable tbody tr").first


def settle(page: Page, row_count: int) -> None:
    """Wait for a navigated page's table; recover one transient 500.

    A navigation that lands in the tail of the previous test's database
    activity can return an empty error shell. The URL the browser aimed at
    is still the intended one, so refetching it is the retry.
    """
    try:
        expect(rows_of(page)).to_have_count(row_count, timeout=4_000)
    except AssertionError:
        goto_with_retry(page, page.url)
        expect(rows_of(page)).to_have_count(row_count, timeout=LOAD_SLACK)


def studio_page(browser: Browser, live_server, course: Cohort, **context_kwargs) -> Page:
    staff = make_studio_user(
        username=f"sort-staffer-{uuid.uuid4().hex[:8]}",
        roles=("course_operator",),
    )
    client = Client()
    client.force_login(staff)
    context = browser.new_context(**context_kwargs)
    page = context.new_page()
    page.context.add_cookies(
        [
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                "url": live_server.url,
            }
        ]
    )
    goto_with_retry(page, enrollment_list_url(live_server, course))
    return page


def test_sort_is_global_and_survives_pagination_without_javascript(
    browser: Browser, live_server, sort_course: Cohort
) -> None:
    page = studio_page(
        browser,
        live_server,
        sort_course,
        viewport={"width": 1440, "height": 900},
        java_script_enabled=False,
    )
    context = page.context
    try:
        settle(page, PAGE_SIZE)
        # The untouched page honours the legacy ordering: position 28 sits
        # on page two, not first.
        expect(first_row(page)).to_contain_text("page-student-01")

        page.get_by_role("link", name="Sort by score, descending").click()
        assert "sort=total_score" in page.url
        assert "dir=desc" in page.url
        assert "page=" not in page.url, "changing the sort must reset to page one"
        settle(page, PAGE_SIZE)
        # The audit reproduction: the globally highest scorer now leads the
        # first page instead of being unreachable on page two.
        expect(first_row(page)).to_contain_text(TOP_SCORER)
        score_header = page.locator("th", has_text="Score").first
        expect(score_header).to_have_attribute("aria-sort", "descending")
        expect(page.locator("th[aria-sort]")).to_have_count(1)
        page.screenshot(path=str(EVIDENCE / "score-desc-page-one.png"), full_page=True)

        # The sort rides through pagination: page two continues the global
        # sequence instead of restarting it.
        page.locator('nav[aria-label="Enrollment pages"] a', has_text="2").click()
        assert "page=2" in page.url
        assert "sort=total_score" in page.url
        assert "dir=desc" in page.url
        settle(page, ROW_COUNT - PAGE_SIZE)
        # Remaining scores 75..70 belong to students 25, 26, 27, 29, 30 —
        # the global sequence continues, minus the promoted top scorer.
        expect(first_row(page)).to_contain_text("page-student-25")

        # Another column starts from its documented default direction.
        page.get_by_role("link", name="Sort by position, ascending").click()
        assert "sort=position" in page.url
        assert "dir=asc" in page.url
        assert "page=" not in page.url
        settle(page, PAGE_SIZE)
        expect(first_row(page)).to_contain_text("page-student-01")
        expect(page.locator("th", has_text="Pos").first).to_have_attribute("aria-sort", "ascending")
        page.screenshot(path=str(EVIDENCE / "position-asc-reset.png"), full_page=True)
    finally:
        context.close()


def test_sort_is_keyboard_operable_and_announces_state(
    browser: Browser, live_server, sort_course: Cohort
) -> None:
    page = studio_page(browser, live_server, sort_course, viewport={"width": 1440, "height": 900})
    context = page.context
    try:
        settle(page, PAGE_SIZE)

        score_link = page.get_by_role("link", name="Sort by score, descending")
        score_link.focus()
        with page.expect_navigation():
            page.keyboard.press("Enter")
        assert "sort=total_score" in page.url
        settle(page, PAGE_SIZE)
        expect(first_row(page)).to_contain_text(TOP_SCORER)

        # The active control now offers the opposite direction, and its
        # column announces the current one.
        toggled = page.get_by_role("link", name="Sort by score, ascending")
        expect(toggled).to_be_visible()
        toggled.focus()
        with page.expect_navigation():
            page.keyboard.press("Enter")
        assert "dir=asc" in page.url
        settle(page, PAGE_SIZE)
        expect(first_row(page)).to_contain_text("page-student-30")
        expect(page.locator("th", has_text="Score").first).to_have_attribute(
            "aria-sort", "ascending"
        )
    finally:
        context.close()


def test_sorted_mobile_view_keeps_the_table_usable(
    browser: Browser, live_server, sort_course: Cohort
) -> None:
    page = studio_page(
        browser,
        live_server,
        sort_course,
        viewport={"width": 390, "height": 844},
        java_script_enabled=False,
    )
    context = page.context
    try:
        settle(page, PAGE_SIZE)
        page.get_by_role("link", name="Sort by score, descending").click()
        assert "sort=total_score" in page.url
        settle(page, PAGE_SIZE)
        expect(first_row(page)).to_contain_text(TOP_SCORER)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        page.screenshot(path=str(EVIDENCE / "score-desc-mobile.png"), full_page=True)
    finally:
        context.close()
