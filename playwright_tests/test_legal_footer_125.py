from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect

from playwright_tests.accessibility_support import axe_issues, structure_issues

pytestmark = [pytest.mark.core, pytest.mark.django_db(transaction=True)]

SCREENSHOT_DIR = Path(".tmp/screenshots/issue-125")
LEGAL_PAGES = (
    ("/terms", "Terms of Service", "terms"),
    ("/privacy", "Privacy Policy", "privacy"),
    ("/impressum", "Impressum", "impressum"),
)
VIEWPORTS = (
    ("desktop", {"width": 1440, "height": 900}),
    ("mobile", {"width": 390, "height": 844}),
)
THEMES = ("light", "dark")
OPTIONAL_COOKIE_NAMES = {
    "_ga",
    "_gid",
    "_gcl_au",
    "dtc_analytics_probe",
    "dtc_attribution_probe",
}


def _cookie_map(page: Page) -> dict[str, str]:
    return {cookie["name"]: cookie["value"] for cookie in page.context.cookies()}


def _deny_initial_prompt(page: Page) -> None:
    dialog = page.get_by_test_id("analytics-preferences-dialog")
    if dialog.is_visible():
        page.get_by_role("button", name="Keep analytics off").click()
        expect(dialog).to_be_hidden()


def _assert_no_horizontal_overflow(page: Page) -> None:
    result = page.evaluate(
        """
        () => ({
          width: window.innerWidth,
          scrollWidth: Math.max(
            document.documentElement.scrollWidth,
            document.body.scrollWidth
          ),
          sources: [...document.querySelectorAll('body *')]
            .filter((element) => {
              const rect = element.getBoundingClientRect();
              return rect.right > window.innerWidth + 1 || rect.left < -1;
            })
            .slice(0, 8)
            .map((element) => {
              const rect = element.getBoundingClientRect();
              return `${element.tagName.toLowerCase()}.${element.className}`
                + ` [${Math.round(rect.left)}, ${Math.round(rect.right)}]`;
            }),
        })
        """
    )
    assert result["scrollWidth"] <= result["width"] + 1, result


def _assert_footer_runtime_alignment(page: Page) -> None:
    runtime = page.locator(".site-footer-runtime")
    github_box = runtime.locator(".site-footer-github").bounding_box()
    version_box = runtime.locator(".footer-version").bounding_box()
    assert github_box is not None
    assert version_box is not None
    github_center = github_box["y"] + github_box["height"] / 2
    version_center = version_box["y"] + version_box["height"] / 2
    assert abs(github_center - version_center) <= 1


@pytest.mark.parametrize(("viewport_name", "viewport"), VIEWPORTS)
@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize(("path", "heading", "name"), LEGAL_PAGES)
def test_legal_page_visual_matrix_and_footer_contract(
    page: Page,
    live_server,
    viewport_name: str,
    viewport: dict[str, int],
    theme: str,
    path: str,
    heading: str,
    name: str,
) -> None:
    page.set_viewport_size(viewport)
    stored_theme = "true" if theme == "dark" else "false"
    page.add_init_script(f"localStorage.setItem('darkMode', '{stored_theme}');")
    response = page.goto(f"{live_server.url}{path}", wait_until="domcontentloaded")
    assert response is not None and response.status == 200
    _deny_initial_prompt(page)
    expect(page.get_by_role("heading", name=heading, exact=True)).to_be_visible()
    expect(page.locator('link[rel="canonical"]')).to_have_attribute(
        "href", f"https://datatalks.club{path}"
    )
    legal = page.get_by_role("navigation", name="Legal")
    expect(legal.get_by_role("link", name="Terms of Service")).to_have_attribute("href", "/terms")
    expect(legal.get_by_role("link", name="Privacy Policy")).to_have_attribute("href", "/privacy")
    expect(legal.get_by_role("link", name="Impressum")).to_have_attribute("href", "/impressum")
    expect(legal.get_by_role("button", name="Analytics preferences")).to_be_visible()
    for control in legal.locator("a, button").all():
        box = control.bounding_box()
        assert box is not None
        assert box["height"] >= 44
    _assert_no_horizontal_overflow(page)
    assert structure_issues(page, f"issue125.{name}.{viewport_name}.{theme}") == []
    assert axe_issues(page, f"issue125.{name}.{viewport_name}.{theme}") == []
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(
        path=SCREENSHOT_DIR / f"{name}-{viewport_name}-{theme}.png",
        full_page=True,
    )


def test_consent_keyboard_allow_reopen_escape_withdraw_and_cookie_cleanup(
    page: Page,
    live_server,
) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    page.add_init_script("localStorage.setItem('darkMode', 'true');")
    outbound_requests: list[str] = []
    page.on(
        "request",
        lambda request: (
            outbound_requests.append(request.url)
            if not request.url.startswith(live_server.url)
            else None
        ),
    )
    page.goto(f"{live_server.url}/terms", wait_until="networkidle")
    dialog = page.get_by_test_id("analytics-preferences-dialog")
    expect(dialog).to_be_visible()
    assert not dialog.evaluate("element => element.matches(':modal')")
    assert page.evaluate("document.activeElement === document.body")
    page.keyboard.press("Tab")
    expect(page.locator(".skip-link")).to_be_focused()
    # The legal pages joined design 5a with issue #179, so the navigation toggle
    # is the shell's "Menu" button rather than the older shell's "Explore".
    menu = page.get_by_role("button", name="Menu")
    menu.click()
    expect(menu).to_have_attribute("aria-expanded", "true")
    menu.click()
    expect(menu).to_have_attribute("aria-expanded", "false")
    assert "dtc_analytics_consent" not in _cookie_map(page)
    assert not (_cookie_map(page).keys() & OPTIONAL_COOKIE_NAMES)
    assert outbound_requests == []
    dialog.scroll_into_view_if_needed()
    for control in dialog.get_by_role("button").all():
        box = control.bounding_box()
        assert box is not None
        assert box["height"] >= 44
    assert structure_issues(page, "issue125.analytics.undecided") == []
    assert axe_issues(page, "issue125.analytics.undecided") == []
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(
        path=SCREENSHOT_DIR / "analytics-preferences-mobile-undecided.png",
        full_page=False,
    )

    opener = page.get_by_role("button", name="Analytics preferences")
    opener.scroll_into_view_if_needed()
    opener.click()
    assert dialog.evaluate("element => element.matches(':modal')")
    expect(page.get_by_role("button", name="Keep analytics off")).to_be_focused()
    allow = page.get_by_role("button", name="Allow analytics")
    allow.focus()
    page.keyboard.press("Enter")
    expect(dialog).to_be_hidden()
    expect(opener).to_be_focused()
    assert _cookie_map(page)["dtc_analytics_consent"] == "v1.allow"
    assert outbound_requests == []

    page.reload(wait_until="networkidle")
    expect(dialog).to_be_hidden()
    assert _cookie_map(page)["dtc_analytics_consent"] == "v1.allow"
    assert outbound_requests == []

    opener.scroll_into_view_if_needed()
    opener.focus()
    page.keyboard.press("Enter")
    expect(dialog).to_be_visible()
    expect(dialog).to_have_attribute("open", "")
    expect(page.get_by_role("button", name="Allow analytics")).to_be_focused()
    expect(dialog.locator("#analytics-preferences-status")).to_have_text(
        "Current choice: analytics allowed."
    )
    assert structure_issues(page, "issue125.analytics.reopened") == []
    assert axe_issues(page, "issue125.analytics.reopened") == []
    for _step in range(6):
        page.keyboard.press("Tab")
        assert dialog.evaluate("element => element.contains(document.activeElement)")
    page.screenshot(
        path=SCREENSHOT_DIR / "analytics-preferences-mobile-reopened.png",
        full_page=False,
    )
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    expect(opener).to_be_focused()

    page.context.add_cookies(
        [
            {"name": name, "value": "synthetic", "url": live_server.url}
            for name in sorted(OPTIONAL_COOKIE_NAMES)
        ]
    )
    opener.press("Enter")
    expect(dialog).to_be_visible()
    page.get_by_role("button", name="Keep analytics off").click()
    expect(dialog).to_be_hidden()
    expect(opener).to_be_focused()
    cookies = _cookie_map(page)
    assert cookies["dtc_analytics_consent"] == "v1.deny"
    assert not (cookies.keys() & OPTIONAL_COOKIE_NAMES)
    assert outbound_requests == []

    page.get_by_role("link", name="Privacy Policy").last.click()
    expect(page).to_have_url(f"{live_server.url}/privacy")
    expect(page.get_by_role("heading", name="Privacy Policy", exact=True)).to_be_visible()
    assert outbound_requests == []


def test_github_icon_is_never_underlined_and_keeps_keyboard_focus(
    page: Page,
    live_server,
) -> None:
    page.goto(f"{live_server.url}/privacy", wait_until="domcontentloaded")
    _deny_initial_prompt(page)
    github = page.get_by_role("link", name="Website source on GitHub (opens in a new tab)")
    github.scroll_into_view_if_needed()
    expect(github).to_have_attribute("target", "_blank")
    expect(github).to_have_attribute("rel", "noopener noreferrer")

    def decoration() -> str:
        return github.evaluate("element => getComputedStyle(element).textDecorationLine")

    assert decoration() == "none"
    github.hover()
    assert decoration() == "none"

    page.evaluate(
        """
        () => {
          document.body.tabIndex = -1;
          document.body.focus();
        }
        """
    )
    for _step in range(40):
        page.keyboard.press("Tab")
        if github.evaluate("element => element === document.activeElement"):
            break
    else:
        pytest.fail("GitHub footer link was not reachable through keyboard Tab traversal")

    expect(github).to_be_focused()
    assert github.evaluate("element => element.matches(':focus-visible')")
    assert decoration() == "none"
    outline = github.evaluate("element => getComputedStyle(element).outlineStyle")
    outline_color = github.evaluate("element => getComputedStyle(element).outlineColor")
    assert outline != "none"
    assert outline_color != "rgb(255, 191, 71)"
    github.dispatch_event("mousedown")
    assert decoration() == "none"


def test_legal_footer_reflows_at_320px_and_200_percent(page: Page, live_server) -> None:
    page.set_viewport_size({"width": 320, "height": 800})
    page.emulate_media(reduced_motion="reduce")
    page.goto(f"{live_server.url}/terms", wait_until="domcontentloaded")
    _deny_initial_prompt(page)
    cdp = page.context.new_cdp_session(page)
    cdp.send("Emulation.setPageScaleFactor", {"pageScaleFactor": 2})
    assert page.evaluate("visualViewport.scale") == 2
    _assert_no_horizontal_overflow(page)
    expect(page.get_by_role("navigation", name="Legal")).to_be_visible()
    _assert_footer_runtime_alignment(page)
    assert structure_issues(page, "issue125.legal.200-percent") == []
    cdp.send("Emulation.setPageScaleFactor", {"pageScaleFactor": 1})
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / "terms-320-reduced-motion.png", full_page=True)


def test_legal_pages_remain_readable_without_javascript(browser: Browser, live_server) -> None:
    context = browser.new_context(
        java_script_enabled=False,
        viewport={"width": 390, "height": 844},
        reduced_motion="reduce",
    )
    page = context.new_page()
    try:
        for path, heading, _name in LEGAL_PAGES:
            response = page.goto(f"{live_server.url}{path}", wait_until="domcontentloaded")
            assert response is not None and response.status == 200
            expect(page.get_by_role("heading", name=heading, exact=True)).to_be_visible()
            expect(page.get_by_test_id("analytics-preferences-dialog")).to_be_hidden()
            expect(page.get_by_role("navigation", name="Legal")).to_be_visible()
            _assert_no_horizontal_overflow(page)
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=SCREENSHOT_DIR / "impressum-mobile-no-js.png", full_page=True)
    finally:
        context.close()
