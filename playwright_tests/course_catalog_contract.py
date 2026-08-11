from __future__ import annotations

from playwright.sync_api import Locator, Page, expect


def assert_copied_course_catalog_link(
    page: Page,
    *,
    path: str,
    title: str,
) -> Locator:
    """Locate one copied-CMP course row without inventing its accessible name."""

    course_link = page.locator(f'main #courses a[href="{path}"]')
    expect(course_link).to_have_count(1)
    expect(course_link).to_be_visible()
    expect(course_link).to_have_attribute("href", path)
    title_heading = course_link.locator(
        "xpath=ancestor::*[self::h3 or self::h4] | descendant::*[self::h3 or self::h4]"
    )
    expect(title_heading).to_have_count(1)
    expect(title_heading).to_be_visible()
    expect(title_heading).to_have_text(title)
    return course_link
