from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from django.test import override_settings
from playwright.sync_api import Browser, Page, expect

from content.queries import ResolvePublicAsset, resolve_public_asset
from content.services import (
    ActivateContentRelease,
    RollbackContentRelease,
    activate_content_release,
    rollback_content_release,
)
from content.tests.factories import CONTEXT, activate, make_ready_release, make_source

pytestmark = [
    pytest.mark.core,
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("_content_fixture_settings"),
]


@pytest.fixture
def _content_fixture_settings():
    with override_settings(
        ROOT_URLCONF="content.tests.fixture_urls",
        APPEND_SLASH=False,
    ):
        yield


@pytest.mark.parametrize(
    "viewport", [{"width": 1280, "height": 720}, {"width": 390, "height": 844}]
)
def test_exact_content_release_activation_failure_and_rollback(
    browser: Browser,
    page: Page,
    live_server,
    viewport: dict[str, int],
) -> None:
    page.set_viewport_size(viewport)
    source = make_source()
    v1 = activate(source, make_ready_release(source, commit_character="a"))
    v2 = make_ready_release(
        source,
        commit_character="b",
        heading="Fixture release v2",
        marker="commit-v2",
    )
    exact_url = f"{live_server.url}/Fixture/Exact.html"

    response = page.goto(exact_url)
    assert response is not None
    assert response.status == 200
    expect(page.get_by_role("heading", name="Fixture release v1")).to_be_visible()
    expect(page.get_by_text("commit-v1", exact=True)).to_be_visible()
    expect(page.get_by_alt_text("Fixture logo")).to_have_attribute(
        "src", "/assets/Fixture-Logo.svg"
    )
    image = page.get_by_alt_text("Fixture logo")
    expect(image).to_have_js_property("complete", True)
    assert image.evaluate("element => element.naturalWidth") > 0
    for path in ("/fixture/Exact.html", "/Fixture/Exact.html/", "/unknown.html"):
        missing = page.request.get(f"{live_server.url}{path}")
        assert missing.status == 404
        assert "Fixture release" not in missing.text()

    source.refresh_from_db()
    v2.refresh_from_db()
    with patch("content.services._before_release_swap", side_effect=RuntimeError("injected")):
        with pytest.raises(RuntimeError, match="injected"):
            activate_content_release(
                ActivateContentRelease(
                    source.id,
                    v2.id,
                    source.revision,
                    v2.revision,
                ),
                context=CONTEXT,
            )
    page.reload()
    expect(page.get_by_role("heading", name="Fixture release v1")).to_be_visible()

    source.refresh_from_db()
    v2.refresh_from_db()
    activate_content_release(
        ActivateContentRelease(
            source.id,
            v2.id,
            source.revision,
            v2.revision,
            reason="browser fixture activation",
        ),
        context=CONTEXT,
    )
    page.goto(exact_url)
    expect(page.get_by_role("heading", name="Fixture release v2")).to_be_visible()
    expect(page.get_by_text("commit-v2", exact=True)).to_be_visible()
    expect(page.get_by_alt_text("Fixture logo")).to_have_attribute(
        "src", "/assets/Fixture-Logo.svg"
    )
    asset = resolve_public_asset(
        ResolvePublicAsset("/assets/Fixture-Logo.svg"),
        context=CONTEXT,
    )
    assert asset is not None
    assert str(v2.id) in asset.storage_key
    assert asset.checksum == "b" * 64
    screenshot_directory = Path(".tmp/screenshots/issue-37")
    screenshot_directory.mkdir(parents=True, exist_ok=True)
    device = "desktop" if viewport["width"] > 600 else "mobile"
    page.screenshot(
        path=screenshot_directory / f"active-v2-{device}.png",
        full_page=True,
    )

    no_js_context = browser.new_context(java_script_enabled=False, viewport=viewport)
    try:
        no_js = no_js_context.new_page()
        no_js_response = no_js.goto(exact_url)
        assert no_js_response is not None
        assert no_js_response.status == 200
        expect(no_js.get_by_role("heading", name="Fixture release v2")).to_be_visible()
        expect(no_js.get_by_text("commit-v2", exact=True)).to_be_visible()
    finally:
        no_js_context.close()

    source.refresh_from_db()
    v1.refresh_from_db()
    rollback_content_release(
        RollbackContentRelease(
            source.id,
            v1.id,
            source.revision,
            v1.revision,
            "browser fixture rollback",
        ),
        context=CONTEXT,
    )
    page.goto(exact_url)
    expect(page.get_by_role("heading", name="Fixture release v1")).to_be_visible()
    expect(page.get_by_text("commit-v1", exact=True)).to_be_visible()
    rolled_back_asset = resolve_public_asset(
        ResolvePublicAsset("/assets/Fixture-Logo.svg"),
        context=CONTEXT,
    )
    assert rolled_back_asset is not None
    assert str(v1.id) in rolled_back_asset.storage_key
    assert rolled_back_asset.checksum == "a" * 64
    page.screenshot(
        path=screenshot_directory / f"rolled-back-v1-{device}.png",
        full_page=True,
    )
