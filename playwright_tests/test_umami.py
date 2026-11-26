"""Exercise consent and payload guards in a browser without contacting analytics."""

from pathlib import Path
from urllib.parse import urlsplit

import pytest
from django.template.loader import render_to_string
from playwright.sync_api import Page, expect

from core.middleware import SECURITY_HEADERS
from core.source_policy import UMAMI_ORIGIN, UMAMI_WEBSITE_ID, umami_public_path

pytestmark = pytest.mark.core


def install_page(page: Page, host: str = "prod.datatalks.club", path: str = "/privacy") -> list:
    footer = render_to_string(
        "core/_site_footer.html",
        {
            "umami_script_url": (
                f"{UMAMI_ORIGIN}/script.js" if umami_public_path(urlsplit(path).path) else ""
            ),
            "umami_website_id": UMAMI_WEBSITE_ID,
            "umami_page_path": urlsplit(path).path,
        },
    )
    html = (
        "<!doctype html><html><head><title>Synthetic private title</title></head>"
        f"<body>{footer}<script src='/preferences.js'></script></body></html>"
    )
    script = Path("core/static/core/analytics_preferences.js").read_text()
    requests = []
    # This provider double calls the documented before-send hook with a hostile
    # payload. The assertion verifies the boundary, not provider implementation.
    provider = """window.umami = {track: function () {
      var payload = window.dtcUmamiBeforeSend('event', {
        title: document.title, url: location.href, referrer: 'https://example.com/?token=synthetic',
        id: 'synthetic-user', data: {email: 'synthetic@example.invalid'}
      });
      if (payload) fetch('/captured', {method: 'POST', body: JSON.stringify(payload)});
    }};"""

    def route(request_route):
        request = request_route.request
        if request.url == f"{UMAMI_ORIGIN}/script.js":
            requests.append("script")
            request_route.fulfill(body=provider, content_type="application/javascript")
        elif request.url.endswith("/preferences.js"):
            request_route.fulfill(body=script, content_type="application/javascript")
        elif request.url.endswith("/captured"):
            requests.append(request.post_data_json)
            request_route.fulfill(body="{}", content_type="application/json")
        else:
            request_route.fulfill(
                body=html,
                content_type="text/html",
                headers={
                    "Content-Security-Policy": SECURITY_HEADERS["Content-Security-Policy"],
                },
            )

    page.route("**/*", route)
    page.goto(f"https://{host}{path}", wait_until="networkidle")
    return requests


@pytest.mark.parametrize(
    "path",
    [
        "/privacy",
        "/blog/safe-slug.html",
        "/books/safe-slug.html",
        "/podcast/s01/e01-safe-slug",
        "/podcast/safe-slug.html",
        "/people/safe-person.html",
        "/wiki/safe-topic",
        "/courses/safe-course",
        "/events/123/safe-event",
        "/faq/safe-course.html",
    ],
)
def test_consent_allow_minimizes_payload_and_withdrawal_blocks_later_sends(page: Page, path: str):
    requests = install_page(page, path=path + "?utm_source=synthetic#synthetic-fragment")
    assert requests == []
    page.get_by_role("button", name="Allow analytics", exact=True).click()
    expect(page.locator("script[data-website-id]")).to_have_attribute("data-auto-track", "false")
    page.wait_for_function("window.umami !== undefined")
    page.wait_for_timeout(50)
    assert requests == [
        "script",
        {
            "website": UMAMI_WEBSITE_ID,
            "hostname": "prod.datatalks.club",
            "url": path,
            "language": "en-US",
            "screen": page.evaluate("screen.width + 'x' + screen.height"),
        },
    ]
    page.evaluate("history.pushState({}, '', '/register/synthetic-token')")
    assert page.evaluate("window.dtcUmamiBeforeSend('event', {})") is None
    page.evaluate("history.back()")
    page.wait_for_url("**" + path + "?utm_source=synthetic#synthetic-fragment")
    page.get_by_role("button", name="Analytics preferences", exact=True).click()
    page.get_by_role("button", name="Keep analytics off", exact=True).click()
    page.evaluate("window.umami.track()")
    assert len(requests) == 2
    page.reload(wait_until="networkidle")
    assert len(requests) == 2


@pytest.mark.parametrize(
    "host,path",
    [
        ("dev.datatalks.club", "/privacy"),
        ("localhost", "/privacy"),
        ("preview.datatalks.club", "/privacy"),
        ("prod.datatalks.club", "/accounts/login/"),
        ("prod.datatalks.club", "/courses/ml/2026/homework/1"),
        ("prod.datatalks.club", "/register/synthetic-token"),
    ],
)
def test_consent_cannot_enable_private_or_nonproduction_tracking(page: Page, host: str, path: str):
    requests = install_page(page, host=host, path=path)
    page.get_by_role("button", name="Allow analytics", exact=True).click()
    assert requests == []
    assert page.evaluate("window.dtcUmamiBeforeSend('event', {})") is None


@pytest.mark.parametrize("value", ["v1.deny", "v0.allow", "allow", "malformed"])
def test_unknown_or_denied_consent_does_not_load_provider(page: Page, value: str):
    page.context.add_cookies(
        [{"name": "dtc_analytics_consent", "value": value, "url": "https://prod.datatalks.club"}]
    )
    assert install_page(page) == []
