import re
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, ViewportSize, expect

from events.queries import published_event_records

pytestmark = [pytest.mark.core, pytest.mark.django_db(transaction=True)]

SCREENSHOTS = Path(".tmp/screenshots/issue-131")


def _described_recorded_event() -> dict:
    """A published event that carries a description and an external recording.

    The two shapes this module contrasts are properties of the row, not of any
    one event, so read them from the published records rather than naming the
    titles one corpus happened to publish.
    """

    return next(
        event for event in published_event_records() if event["description_html"] and event["links"]
    )


def _undescribed_event() -> dict:
    """A published event with no description and no external links at all."""

    return next(
        event
        for event in published_event_records()
        if not event["description_html"] and not event["links"]
    )


def _recording_name(event: dict) -> str:
    """The accessible name the detail page gives that event's recording link."""

    return f"{event['links'][0]['label']} (opens in a new tab)"


def _screenshot(page: Page, name: str) -> None:
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOTS / name, full_page=True)


def _assert_no_provider_action(page: Page) -> None:
    expect(page.locator('a[href*="luma.com"], a[href*="lu.ma"]')).to_have_count(0)
    expect(page.locator('a[href*="images.lumacdn.com"]')).to_have_count(0)
    expect(page.get_by_role("link", name="Register", exact=True)).to_have_count(0)
    expect(page.get_by_role("button", name="Register", exact=True)).to_have_count(0)
    expect(page.locator('a[href$="/register"]')).to_have_count(0)
    expect(page.locator('form[action*="register"]')).to_have_count(0)


@pytest.mark.core
@pytest.mark.parametrize(
    ("viewport", "suffix"),
    [
        ({"width": 1440, "height": 900}, "desktop"),
        ({"width": 390, "height": 844}, "mobile"),
    ],
)
def test_described_recorded_and_undescribed_event_details(
    page: Page,
    live_server,
    viewport: ViewportSize,
    suffix: str,
) -> None:
    page.set_viewport_size(viewport)
    requests: list[str] = []
    console_errors: list[str] = []
    page.on("request", lambda request: requests.append(request.url))
    page.on(
        "console",
        lambda message: console_errors.append(message.text) if message.type == "error" else None,
    )

    described = _described_recorded_event()
    undescribed = _undescribed_event()
    described_path = described["public_path"]
    undescribed_path = undescribed["public_path"]
    response = page.goto(f"{live_server.url}{described_path}")
    assert response is not None and response.status == 200
    expect(page.get_by_role("heading", name=described["title"], exact=True)).to_be_visible()
    expect(page.locator('section[aria-label="Event description"]')).to_have_count(1)
    recording = page.get_by_role("link", name=_recording_name(described))
    expect(recording).to_be_visible()
    expect(recording).to_have_attribute("target", "_blank")
    expect(recording).to_have_attribute("rel", "noopener noreferrer")
    expect(page.locator('link[rel="canonical"]')).to_have_attribute(
        "href",
        f"https://datatalks.club{described_path}",
    )
    _assert_no_provider_action(page)
    recording.focus()
    assert recording.evaluate("element => getComputedStyle(element).outlineStyle") != "none"
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    _screenshot(page, f"described-recording-{suffix}.png")

    response = page.goto(f"{live_server.url}{undescribed_path}")
    assert response is not None and response.status == 200
    expect(page.get_by_role("heading", name=undescribed["title"], exact=True)).to_be_visible()
    expect(page.locator('section[aria-label="Event description"]')).to_have_count(0)
    expect(page.get_by_role("heading", name="Event links", exact=True)).to_have_count(0)
    expect(page.locator('link[rel="canonical"]')).to_have_attribute(
        "href",
        f"https://datatalks.club{undescribed_path}",
    )
    _assert_no_provider_action(page)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    _screenshot(page, f"undescribed-no-external-links-{suffix}.png")

    assert not any(
        provider in request.casefold()
        for request in requests
        for provider in ("luma.com", "lu.ma", "images.lumacdn.com")
    )
    assert console_errors == []


@pytest.mark.core
def test_event_descriptions_are_meaningful_without_javascript(
    browser: Browser,
    live_server,
) -> None:
    context = browser.new_context(
        java_script_enabled=False,
        viewport={"width": 390, "height": 844},
        reduced_motion="reduce",
    )
    page = context.new_page()
    try:
        described = _described_recorded_event()
        response = page.goto(f"{live_server.url}{described['public_path']}")
        assert response is not None and response.status == 200
        expect(page.get_by_role("heading", name=described["title"], exact=True)).to_be_visible()
        expect(page.locator('section[aria-label="Event description"]')).to_have_count(1)
        expect(page.get_by_role("link", name=_recording_name(described))).to_be_visible()
        _assert_no_provider_action(page)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        _screenshot(page, "no-javascript-described-mobile.png")

        undescribed = _undescribed_event()
        response = page.goto(f"{live_server.url}{undescribed['public_path']}")
        assert response is not None and response.status == 200
        expect(page.get_by_role("heading", name=undescribed["title"], exact=True)).to_be_visible()
        expect(page.locator('section[aria-label="Event description"]')).to_have_count(0)
        _assert_no_provider_action(page)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        _screenshot(page, "no-javascript-undescribed-mobile.png")
    finally:
        context.close()


@pytest.mark.core
def test_event_description_reflows_at_narrow_and_zoom_equivalent_widths(
    page: Page,
    live_server,
) -> None:
    for width in (640, 320):
        page.set_viewport_size({"width": width, "height": 900})
        response = page.goto(f"{live_server.url}{_described_recorded_event()['public_path']}")
        assert response is not None and response.status == 200
        expect(page.locator('section[aria-label="Event description"]')).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.core
def test_event_details_inherit_dark_mode_without_provider_actions(
    page: Page,
    live_server,
) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    described_path = _described_recorded_event()["public_path"]
    undescribed_path = _undescribed_event()["public_path"]
    response = page.goto(f"{live_server.url}{described_path}")
    assert response is not None and response.status == 200
    page.locator("#dark-mode-toggle:visible").click()
    expect(page.locator("body")).to_have_class(re.compile(r"\bdark-mode\b"))
    expect(page.locator('section[aria-label="Event description"]')).to_be_visible()
    _assert_no_provider_action(page)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    _screenshot(page, "described-recording-dark-desktop.png")

    page.set_viewport_size({"width": 390, "height": 844})
    response = page.goto(f"{live_server.url}{undescribed_path}")
    assert response is not None and response.status == 200
    expect(page.locator("body")).to_have_class(re.compile(r"\bdark-mode\b"))
    expect(page.locator('section[aria-label="Event description"]')).to_have_count(0)
    _assert_no_provider_action(page)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    _screenshot(page, "undescribed-no-external-links-dark-mobile.png")
