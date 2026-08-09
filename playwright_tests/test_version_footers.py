from __future__ import annotations

import os
from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client
from playwright.sync_api import Page, expect

from accounts.studio_test_support import make_studio_user

pytestmark = [pytest.mark.core, pytest.mark.django_db(transaction=True)]

VIEWPORTS = (
    ("desktop", {"width": 1440, "height": 900}),
    ("mobile", {"width": 390, "height": 844}),
)


def _authenticate(page: Page, live_server, *, suffix: str) -> None:
    user = make_studio_user(username=f"version-footer-{suffix}", roles=("site_admin",))
    client = Client()
    client.force_login(user)
    page.context.add_cookies(
        [
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                "url": live_server.url,
            }
        ]
    )


def _capture(page: Page, name: str, viewport_name: str) -> None:
    configured = os.environ.get("DTC_VERSION_SCREENSHOT_DIR")
    if configured is None:
        return
    directory = Path(configured)
    directory.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=directory / f"{name}-{viewport_name}.png", full_page=True)


@pytest.mark.parametrize(("viewport_name", "viewport"), VIEWPORTS)
def test_all_three_shells_show_only_the_readable_version(
    page: Page,
    live_server,
    viewport_name: str,
    viewport: dict[str, int],
) -> None:
    page.set_viewport_size(viewport)

    for name, path in (("public", "/"), ("courses", "/courses/")):
        response = page.goto(f"{live_server.url}{path}")
        assert response is not None
        assert response.status == 200
        footer = page.locator("footer")
        expect(footer).to_contain_text(f"Version {settings.VERSION}")
        expect(footer).not_to_contain_text("sha256:")
        assert page.locator(".footer-version-value").evaluate(
            "element => element.scrollWidth <= element.clientWidth + 1"
        )
        _capture(page, name, viewport_name)

    _authenticate(page, live_server, suffix=viewport_name)
    response = page.goto(f"{live_server.url}/studio/")
    assert response is not None
    assert response.status == 200
    footer = page.locator("footer")
    expect(footer).to_contain_text(f"Version {settings.VERSION}")
    expect(footer).not_to_contain_text("sha256:")
    _capture(page, "studio", viewport_name)
