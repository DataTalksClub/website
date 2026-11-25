"""Reach a primary navigation link the way a visitor does at any width.

The masthead groups its entries (Learn, Community, Read).  Below 69rem every
group is open inside the Menu panel; from 69rem up each group is a disclosure
button, so a link is only visible once its group's panel is open.
"""

from __future__ import annotations

from playwright.sync_api import Locator, Page, expect


def primary_navigation_link(page: Page, name: str) -> Locator:
    """Return the visible masthead link called ``name``, opening what hides it."""

    links = page.locator("#site-navigation-links")
    link = links.get_by_role("link", name=name, exact=True)
    menu = page.get_by_role("button", name="Menu")
    if menu.is_visible() and menu.get_attribute("aria-expanded") != "true":
        menu.click()
    # A link in a closed panel is not in the accessibility tree, so the group is
    # found with hidden links included.  ``has`` is matched inside each group, so
    # it takes a locator with no root of its own.
    hidden_link = page.get_by_role("link", name=name, exact=True, include_hidden=True)
    group_toggle = links.locator(".nav-group").filter(has=hidden_link).locator(".nav-group-toggle")
    if group_toggle.count() and group_toggle.is_visible():
        if group_toggle.get_attribute("aria-expanded") != "true":
            group_toggle.click()
    expect(link).to_be_visible()
    return link
