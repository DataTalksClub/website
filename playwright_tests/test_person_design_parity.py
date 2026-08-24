"""Design 5a parity for the public person profile (issue #179).

The profile carries its own inline stylesheet, so this checks what only a browser
can: the shared palette actually paints, both themes hold, nothing overflows at
the design's two viewports, and the page holds its shape for a person with a wide
body of work as well as for one with none at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from content.person_content import person_view
from content.public_data import public_projection

pytestmark = [pytest.mark.full, pytest.mark.django_db(transaction=True)]

SCREENSHOTS = Path(".tmp/screenshots/issue-179/person")
# The widest body of work in the catalogue, and a profile with none at all.
RICH_SLUG = "alexeygrigorev"
SPARSE_SLUG = "aaronwishnick"
# The design 5a page ground: the warm band marks where the page starts, and a
# profile's body of work is the cool lavender content ground that also ends the
# page, so `--page` follows it (`_docs/design/design-5a.md`).  The dark theme
# keeps the partial's own `--page` ground.
LIGHT_BACKGROUND = "rgb(239, 241, 252)"
DARK_BACKGROUND = "rgb(19, 22, 42)"
VIEWPORTS = (
    ({"width": 1440, "height": 900}, "desktop"),
    ({"width": 390, "height": 844}, "mobile"),
)


def _profile(slug: str) -> dict:
    return public_projection()["people_by_slug"][slug]


def _shot(page: Page, name: str) -> None:
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOTS / name, full_page=True)


def _settle_analytics_preferences(page: Page) -> None:
    preferences = page.get_by_role("dialog", name="Optional analytics")
    if preferences.is_visible():
        preferences.get_by_role("button", name="Keep analytics off").click()
        expect(preferences).to_be_hidden()


def _assert_no_horizontal_overflow(page: Page) -> None:
    overflow = page.evaluate(
        """() => ({
          viewport: document.documentElement.clientWidth,
          content: document.documentElement.scrollWidth,
          offenders: [...document.querySelectorAll('body *')]
            .filter((node) => {
              const rect = node.getBoundingClientRect();
              return rect.right > document.documentElement.clientWidth + 0.5;
            })
            .slice(0, 5)
            .map((node) => `${node.tagName.toLowerCase()}.${String(node.className)}`),
        })"""
    )
    assert overflow["content"] <= overflow["viewport"], overflow


@pytest.mark.parametrize(("viewport", "suffix"), VIEWPORTS)
def test_rich_and_sparse_profiles_render_the_design_system_in_both_themes(
    page: Page,
    live_server,
    viewport: dict[str, int],
    suffix: str,
) -> None:
    page.set_viewport_size(viewport)
    origin = live_server.url
    console_errors: list[str] = []
    page.on(
        "console",
        lambda message: console_errors.append(message.text) if message.type == "error" else None,
    )

    for slug, label in ((RICH_SLUG, "rich"), (SPARSE_SLUG, "sparse")):
        record = _profile(slug)
        response = page.goto(f"{origin}{record['public_path']}", wait_until="networkidle")
        assert response is not None and response.status == 200
        _settle_analytics_preferences(page)
        expect(page.locator('link[rel="stylesheet"]')).to_have_count(0)
        expect(page.locator("main h1")).to_have_count(1)
        expect(page.get_by_role("heading", level=1, name=record["title"])).to_be_visible()
        expect(page.locator("body")).to_have_css("background-color", LIGHT_BACKGROUND)
        expect(page.locator("body")).not_to_contain_text("Traceback")
        _assert_no_horizontal_overflow(page)
        _shot(page, f"person-{label}-{suffix}-light.png")

        page.locator("#dark-mode-toggle").click()
        expect(page.locator("body.dark-mode")).to_have_count(1)
        expect(page.locator("body")).to_have_css("background-color", DARK_BACKGROUND)
        _assert_no_horizontal_overflow(page)
        _shot(page, f"person-{label}-{suffix}-dark.png")
        page.locator("#dark-mode-toggle").click()
        expect(page.locator("body.dark-mode")).to_have_count(0)

    assert console_errors == []


def test_a_wide_body_of_work_is_grouped_into_scannable_rows(page: Page, live_server) -> None:
    record = _profile(RICH_SLUG)
    person = person_view(record)
    page.goto(f"{live_server.url}{record['public_path']}", wait_until="networkidle")
    _settle_analytics_preferences(page)

    # Every contribution is the site's shared archive row.
    expect(page.locator(".list-row.archive-row.person-row")).to_have_count(
        len(record["relationships"])
    )
    expect(page.locator(".stat-tile")).to_have_count(len(person.groups))
    expect(page.locator(".person-rows-podcast .play-disc")).to_have_count(5)
    expect(page.locator(".person-rows-events .date-rail")).to_have_count(50)
    for group in person.groups:
        heading = page.locator(f"#{group.anchor}-heading")
        expect(heading).to_be_visible()
        expect(heading).to_have_text(group.heading)

    first_row_link = page.locator(".archive-title a").first
    expect(first_row_link).to_have_attribute("href", person.groups[0].items[0].public_path)

    portrait = page.get_by_role("img", name=f"Portrait of {record['title']}")
    expect(portrait).to_be_visible()


def test_a_long_group_folds_and_opens_without_javascript_of_its_own(
    page: Page,
    live_server,
) -> None:
    """Fifty events is a wall: six stay in view and the rest are one click away."""

    record = _profile(RICH_SLUG)
    person = person_view(record)
    events = next(group for group in person.groups if group.key == "events")
    page.goto(f"{live_server.url}{record['public_path']}", wait_until="networkidle")
    _settle_analytics_preferences(page)

    fold = page.locator("#person-events-more")
    control = fold.locator("summary")
    expect(control).to_be_visible()
    expect(control).to_contain_text(events.fold_label)

    hidden = fold.locator(".list-row")
    expect(hidden).to_have_count(events.folded_count)
    expect(hidden.first).to_be_hidden()
    # The visible rows are the ones outside the fold.
    expect(page.locator(".person-rows-events > .list-row")).to_have_count(len(events.visible_items))

    # The control is the browser's own: it is reachable and operable by keyboard.
    box = control.bounding_box()
    assert box is not None and box["height"] >= 44, box
    control.click()
    expect(hidden.first).to_be_visible()
    expect(control).to_contain_text(events.fold_close_label)
    _assert_no_horizontal_overflow(page)
    control.click()
    expect(hidden.first).to_be_hidden()

    # A group short enough to read offers no control at all.
    expect(page.locator(".person-rows-podcast .row-fold")).to_have_count(0)


def test_external_profile_links_announce_that_they_open_a_new_tab(
    page: Page,
    live_server,
) -> None:
    record = _profile(RICH_SLUG)
    page.goto(f"{live_server.url}{record['public_path']}", wait_until="networkidle")
    _settle_analytics_preferences(page)

    links = page.locator("nav[aria-label='Public profile links'] a")
    expect(links).to_have_count(len(record["links"]))
    for index, link in enumerate(record["links"]):
        control = links.nth(index)
        expect(control).to_have_attribute("href", link["url"])
        expect(control).to_have_attribute("target", "_blank")
        expect(control).to_have_attribute("rel", "noopener noreferrer")
        expect(control).to_contain_text(link["label"])
        # The system's minimum target height, on the profile's own controls.
        box = control.bounding_box()
        assert box is not None and box["height"] >= 44, (link["label"], box)

    # Reached by keyboard from the main landmark, and outlined once it is: a
    # programmatic focus() would never satisfy :focus-visible.
    page.locator("#main-content").focus()
    first_url = record["links"][0]["url"]
    for _ in range(20):
        page.keyboard.press("Tab")
        if page.evaluate("() => document.activeElement.getAttribute('href')") == first_url:
            break
    else:
        raise AssertionError("the first profile link is not reachable by keyboard")
    outline = page.evaluate(
        "() => getComputedStyle(document.activeElement).outlineStyle",
    )
    assert outline != "none"


def test_a_profile_without_linked_work_says_so(page: Page, live_server) -> None:
    record = _profile(SPARSE_SLUG)
    page.goto(f"{live_server.url}{record['public_path']}", wait_until="networkidle")
    _settle_analytics_preferences(page)

    expect(page.locator(".row-list")).to_have_count(0)
    expect(page.locator(".stat-tile")).to_have_count(0)
    expect(page.locator(".person-empty")).to_contain_text(
        f"No podcast episode, event, article or book on DataTalks.Club lists {record['title']} yet."
    )
