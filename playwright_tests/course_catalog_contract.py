from __future__ import annotations

from playwright.sync_api import Locator, Page, expect


def assert_copied_course_catalog_link(
    page: Page,
    *,
    path: str,
    title: str,
) -> Locator:
    """Locate one course in the catalogue by its own route and heading.

    Design 5a (issue #179) gives the courses index three differently shaped surfaces —
    the active cohort card, the open-registration card and the finished-course row — so the
    contract is that the course is reachable at its own path and is announced by a
    heading carrying its title, not that a particular card nests the two a fixed way.
    """

    course_link = page.locator(f'main #courses a[href="{path}"]').first
    expect(course_link).to_be_visible()
    expect(course_link).to_have_attribute("href", path)
    title_heading = page.locator("main #courses").get_by_role("heading", name=title, exact=True)
    expect(title_heading).to_have_count(1)
    expect(title_heading).to_be_visible()
    return course_link
