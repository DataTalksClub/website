"""The tablet tier is laid out, not a stretched phone.

Between a phone and a desktop (36rem to 61.99rem; a half-screen browser
window is about 705px) a card grid shows two or more cards a row, an action
keeps its own width instead of the phone's full-width bar, and a picture is
never drawn larger than it is on a desktop (_docs/design/design-system.md,
"Responsive rules").  The phone layout below 36rem is unchanged.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page

pytestmark = [pytest.mark.core, pytest.mark.django_db(transaction=True)]

HIDE_CONSENT = "body dialog, body [class*=consent]{display:none!important}"
TABLET = {"width": 705, "height": 900}
PHONE = {"width": 390, "height": 844}


def _open(page: Page, url: str, viewport: dict[str, int]) -> None:
    page.set_viewport_size(viewport)
    response = page.goto(url, wait_until="load")
    assert response is not None and response.status == 200, url
    page.add_style_tag(content=HIDE_CONSENT)


def _boxes(page: Page, selector: str) -> list[dict[str, float]]:
    return page.eval_on_selector_all(
        selector,
        """els => els.map(e => e.getBoundingClientRect())
            .filter(r => r.width > 0)
            .map(r => ({top: Math.round(r.top), width: r.width, height: r.height}))""",
    )


def _items_on_first_row(boxes: list[dict[str, float]]) -> int:
    return sum(1 for box in boxes if box["top"] == boxes[0]["top"])


def test_homepage_at_tablet_width(page: Page, live_server) -> None:
    _open(page, live_server.url + "/", TABLET)
    actions = _boxes(page, ".hero-actions .cta")
    assert len(actions) == 2
    assert all(box["width"] < TABLET["width"] / 2 for box in actions), actions
    assert actions[0]["top"] == actions[1]["top"], "the hero actions share a row"

    hero = _boxes(page, ".hero-figure img.doodle-light")
    assert hero and hero[0]["width"] <= 24 * 16 + 1, hero

    climb = _boxes(page, ".stage-card")
    assert len(climb) == 3
    assert all(box["width"] < TABLET["width"] for box in climb)
    assert all(box["height"] < 300 for box in climb), climb

    tiles = _boxes(page, ".home-event")
    if len(tiles) >= 2:
        assert _items_on_first_row(tiles) >= 2, tiles


def test_homepage_phone_keeps_full_width_actions(page: Page, live_server) -> None:
    _open(page, live_server.url + "/", PHONE)
    content = page.evaluate("document.querySelector('.hero .shell').clientWidth")
    actions = _boxes(page, ".hero-actions .cta")
    assert all(box["width"] > PHONE["width"] * 0.8 for box in actions), (actions, content)


def test_three_up_card_grid_shows_two_a_row_on_a_tablet(page: Page, live_server) -> None:
    """The shared `.card-grid-3` itself, independent of what a page lists."""
    _open(page, live_server.url + "/", TABLET)
    page.evaluate(
        """() => {
          const grid = document.createElement('div');
          grid.className = 'card-grid card-grid-3 tablet-probe';
          grid.innerHTML = '<div class="card">a</div>'.repeat(3);
          document.querySelector('main .shell').append(grid);
        }"""
    )
    cards = _boxes(page, ".tablet-probe > .card")
    assert _items_on_first_row(cards) == 2, cards
    # The odd card left alone takes the whole last row.
    assert cards[2]["width"] > cards[0]["width"] * 1.9, cards


def test_course_catalogue_at_tablet_width(page: Page, live_server) -> None:
    _open(page, live_server.url + "/courses", TABLET)
    collage = _boxes(page, ".hero-collage-card")
    if collage:
        assert _items_on_first_row(collage) == len(collage), collage
    cards = _boxes(page, ".courses-catalog-list > *")
    if len(cards) >= 2:
        assert _items_on_first_row(cards) == 2, cards
