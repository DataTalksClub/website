"""The journey a mail recipient takes from a link in an email.

The link comes from Relay, so this exercise stands a stub in for Relay rather
than reaching one: the bridge's pooled session is replaced in the same process
the live server runs in, so nothing here opens a socket to anything real.

What the browser has to prove, and unit tests cannot:

* the preference page is a page of this site, not a foreign product's page
  served through a different hostname, and it reads at 390px as well as 1440px;
* the unsubscribe form actually submits from a browser with no session, no
  cookie and no CSRF token, which is the only state a mail recipient arrives in;
* neither the page nor the tracking pixel leaves a cookie behind.
"""

from __future__ import annotations

from unittest import mock

import pytest
from playwright.sync_api import Page, expect

from email_app import relay_links
from email_app.tests.support import FakeRelay, unreachable_relay

pytestmark = [pytest.mark.core, pytest.mark.django_db(transaction=True)]

RELAY = "http://relay.website.internal:8000"
TOKEN = "kD3Yy8x-Ug2f_QwErTyUiOpAsDfGhJkLzXcVbNm1234"
UNSUBSCRIBE_PATH = f"/unsubscribe/{TOKEN}"
VIEWPORTS = (
    ("desktop", {"width": 1440, "height": 900}),
    ("mobile", {"width": 390, "height": 844}),
)
# The shared shell's own script records a timezone preference client-side. It is
# the documented non-credential cookie, not something the server set, and the
# server-side no-`Set-Cookie` contract is asserted in the Django tests.
ANONYMOUS_COOKIE_NAMES = {"browser_timezone"}


@pytest.fixture
def relay_bridge(settings):  # type: ignore[no-untyped-def]
    settings.RELAY_LINK_BRIDGE_BASE_URL = RELAY

    def _install(relay: FakeRelay):  # type: ignore[no-untyped-def]
        patcher = mock.patch.object(relay_links, "_pool", return_value=relay)
        patcher.start()
        return patcher

    patchers: list[mock._patch] = []  # type: ignore[type-arg]
    try:
        yield lambda relay: patchers.append(_install(relay))
    finally:
        for patcher in patchers:
            patcher.stop()


def _cookie_names(page: Page) -> set[str]:
    return {cookie["name"] for cookie in page.context.cookies()}


def _assert_no_horizontal_overflow(page: Page) -> None:
    result = page.evaluate(
        """
        () => ({
          width: window.innerWidth,
          scrollWidth: Math.max(
            document.documentElement.scrollWidth,
            document.body.scrollWidth
          ),
        })
        """
    )
    assert result["scrollWidth"] <= result["width"] + 1, result


@pytest.mark.parametrize(("viewport_name", "viewport"), VIEWPORTS, ids=[v[0] for v in VIEWPORTS])
def test_a_recipient_can_read_and_submit_the_preference_page(
    page: Page,
    live_server,  # type: ignore[no-untyped-def]
    relay_bridge,  # type: ignore[no-untyped-def]
    viewport_name: str,
    viewport: dict[str, int],
) -> None:
    relay_bridge(FakeRelay(status_code=200))
    page.set_viewport_size(viewport)

    response = page.goto(f"{live_server.url}{UNSUBSCRIBE_PATH}", wait_until="domcontentloaded")
    assert response is not None and response.status == 200

    # This site's page, in this site's design system.
    expect(page.get_by_role("heading", name="Choose which email to stop")).to_be_visible()
    expect(page.locator(".masthead")).to_be_visible()
    assert "Datamailer" not in page.content()
    # A per-recipient link is not a destination and is never repeated back.
    assert TOKEN not in page.content()
    _assert_no_horizontal_overflow(page)

    # A recipient arrives with nothing: no session and no CSRF cookie.
    assert _cookie_names(page) <= ANONYMOUS_COOKIE_NAMES

    page.get_by_role("radio", name="Stop every marketing email we send").check()
    page.get_by_role("button", name="Confirm").click()

    expect(page.get_by_role("heading", name="You have been unsubscribed")).to_be_visible()
    assert _cookie_names(page) <= ANONYMOUS_COOKIE_NAMES
    _assert_no_horizontal_overflow(page)


def test_an_opt_out_is_still_accepted_when_relay_is_unreachable(
    page: Page,
    live_server,  # type: ignore[no-untyped-def]
    relay_bridge,  # type: ignore[no-untyped-def]
) -> None:
    from email_app.models import PendingUnsubscribe

    relay_bridge(unreachable_relay())

    page.goto(f"{live_server.url}{UNSUBSCRIBE_PATH}", wait_until="domcontentloaded")
    expect(page.get_by_text("We could not check this link just now")).to_be_visible()

    page.get_by_role("button", name="Confirm").click()

    # The person is told yes, and the promise is durable rather than polite.
    expect(page.get_by_role("heading", name="Your request has been recorded")).to_be_visible()
    assert PendingUnsubscribe.objects.filter(scope="client").count() == 1


def test_the_open_pixel_is_a_real_image_that_leaves_no_cookie(
    page: Page,
    live_server,  # type: ignore[no-untyped-def]
    relay_bridge,  # type: ignore[no-untyped-def]
) -> None:
    relay_bridge(FakeRelay(status_code=200))

    response = page.request.get(f"{live_server.url}/t/o/{TOKEN}.gif")

    assert response.status == 200
    assert response.headers["content-type"] == "image/gif"
    assert response.body().startswith(b"GIF89a")
    assert "set-cookie" not in {name.lower() for name in response.headers}
    assert "no-store" in response.headers["cache-control"]


def test_a_verified_click_lands_the_reader_on_the_destination(
    page: Page,
    live_server,  # type: ignore[no-untyped-def]
    relay_bridge,  # type: ignore[no-untyped-def]
) -> None:
    relay_bridge(FakeRelay(status_code=302))
    destination = f"{live_server.url}/courses"

    page.goto(f"{live_server.url}/t/c/{TOKEN}?u={destination}", wait_until="domcontentloaded")

    expect(page).to_have_url(destination)


def test_an_unverifiable_click_shows_the_destination_instead_of_following_it(
    page: Page,
    live_server,  # type: ignore[no-untyped-def]
    relay_bridge,  # type: ignore[no-untyped-def]
) -> None:
    relay_bridge(unreachable_relay())
    destination = f"{live_server.url}/courses"
    click_url = f"{live_server.url}/t/c/{TOKEN}?u={destination}"

    page.goto(click_url, wait_until="domcontentloaded")

    # No silent redirect: the reader stays here and is shown where the link goes.
    expect(page).to_have_url(click_url)
    expect(page.get_by_role("heading", name="We could not check this link")).to_be_visible()
    expect(page.get_by_text(destination, exact=False)).to_be_visible()

    page.get_by_role("link", name="Continue to this address").click()
    expect(page).to_have_url(destination)
