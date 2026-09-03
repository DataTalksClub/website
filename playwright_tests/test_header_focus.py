from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from playwright.sync_api import Page, expect

SCREENSHOTS = Path(".tmp/screenshots/issue-126")
VIEWPORTS = (
    ({"width": 1440, "height": 900}, "desktop"),
    ({"width": 390, "height": 844}, "mobile"),
)


def focus_style(locator) -> dict[str, str | bool]:
    return locator.evaluate(
        """
        element => {
          const style = getComputedStyle(element);
          return {
            focused: element === document.activeElement,
            focusVisible: element.matches(':focus-visible'),
            outlineColor: style.outlineColor,
            outlineStyle: style.outlineStyle,
            outlineWidth: style.outlineWidth,
            linkColor: style.color,
          };
        }
        """
    )


@pytest.mark.full
@pytest.mark.parametrize(("viewport", "suffix"), VIEWPORTS)
@pytest.mark.parametrize("dark_mode", (False, True), ids=("light", "dark"))
def test_header_pointer_and_keyboard_focus_are_visually_distinct(
    page: Page,
    live_server,
    viewport: dict[str, int],
    suffix: str,
    dark_mode: bool,
) -> None:
    page.set_viewport_size(viewport)
    response = page.goto(live_server.url)
    assert response is not None and response.status == 200
    if dark_mode:
        page.locator("body").evaluate("element => element.classList.add('dark', 'dark-mode')")

    if suffix == "mobile":
        pointer_target = page.get_by_role("button", name="Menu")
        pointer_target.click()
        expect(pointer_target).to_have_attribute("aria-expanded", "true")
        keyboard_target = page.locator("#site-navigation-links").get_by_role(
            "link",
            name="Events",
            exact=True,
        )
    else:
        pointer_target = page.locator("#site-navigation-links").get_by_role(
            "link",
            name="Events",
            exact=True,
        )
        pointer_target.evaluate(
            "element => element.addEventListener('click', event => "
            "event.preventDefault(), {once: true})"
        )
        pointer_target.click()
        keyboard_target = page.locator("#site-navigation-links").get_by_role(
            "link",
            name="Courses",
            exact=True,
        )

    expect(pointer_target).to_have_css("outline-width", "0px")
    pointer_style = focus_style(pointer_target)
    assert pointer_style["focused"] is True
    assert pointer_style["focusVisible"] is False
    assert pointer_style["outlineStyle"] == "none"
    assert pointer_style["outlineWidth"] == "0px"
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(
        path=SCREENSHOTS / f"header-pointer-focus-{'dark' if dark_mode else 'light'}-{suffix}.png"
    )

    page.keyboard.press("Tab")
    expect(keyboard_target).to_be_focused()
    expect(keyboard_target).to_have_css("outline-width", "3px")
    keyboard_style = focus_style(keyboard_target)
    assert keyboard_style["focusVisible"] is True
    assert keyboard_style["outlineStyle"] == "solid"
    assert keyboard_style["outlineWidth"] == "3px"
    assert keyboard_style["outlineColor"] == keyboard_style["linkColor"]
    assert keyboard_style["outlineColor"] != "rgb(155, 77, 0)"

    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
    )
    page.screenshot(
        path=SCREENSHOTS / f"header-keyboard-focus-{'dark' if dark_mode else 'light'}-{suffix}.png"
    )


@pytest.mark.full
def test_skip_link_remains_keyboard_visible_without_outlining_main_content(
    page: Page,
    live_server,
) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    response = page.goto(live_server.url)
    assert response is not None and response.status == 200

    skip_link = page.locator(".skip-link")
    page.keyboard.press("Tab")
    expect(skip_link).to_be_focused()
    skip_style = focus_style(skip_link)
    assert skip_style["focusVisible"] is True
    assert skip_style["outlineStyle"] == "solid"

    page.keyboard.press("Enter")
    main = page.locator("#main-content")
    expect(main).to_be_focused()
    main_style = focus_style(main)
    assert main_style["outlineStyle"] == "none"
    assert main_style["outlineWidth"] == "0px"


@pytest.mark.full
@pytest.mark.parametrize("path", ("/", "/courses", "/events", "/podcast"))
def test_account_menu_closes_on_escape_and_on_a_click_outside_it(
    page: Page,
    live_server,
    path: str,
) -> None:
    """The account menu is a <details>, which only closes through its own summary.

    courses/static/user_menu.js gives it the two closes a disclosure menu is
    expected to have.  The design system rebuild dropped that script from every
    rebuilt page (issue #179), so the menu opened and would not close.
    """

    user = get_user_model().objects.create_user(
        username="menu-reader@example.invalid",
        email="menu-reader@example.invalid",
        password="test-password",
    )
    client = Client()
    client.force_login(user)
    page.context.add_cookies(
        [
            {
                "name": "sessionid",
                "value": client.cookies["sessionid"].value,
                "url": live_server.url,
            }
        ]
    )
    page.set_viewport_size({"width": 1440, "height": 900})
    response = page.goto(f"{live_server.url}{path}", wait_until="networkidle")
    assert response is not None and response.status == 200

    menu = page.locator("details.user-menu")
    summary = menu.locator("summary")
    expect(menu).to_have_count(1)

    summary.click()
    expect(menu).to_have_attribute("open", "")
    page.keyboard.press("Escape")
    expect(menu).not_to_have_attribute("open", "")
    expect(summary).to_be_focused()

    summary.click()
    expect(menu).to_have_attribute("open", "")
    page.locator("#main-content").click(position={"x": 5, "y": 5})
    expect(menu).not_to_have_attribute("open", "")
