from __future__ import annotations

from pathlib import Path

import pytest
from django.urls import reverse
from playwright.sync_api import Browser, Page, ViewportSize, expect

from courses.models import Cohort, RegistrationCampaign

pytestmark = [pytest.mark.core, pytest.mark.django_db(transaction=True)]

SCREENSHOTS = Path(".tmp/screenshots/issue-133-engineer")
VIEWPORTS = (
    ({"width": 1440, "height": 900}, "desktop"),
    ({"width": 390, "height": 844}, "mobile"),
)


def _campaign_with_count(settings, count: int) -> RegistrationCampaign:
    del settings  # No registered-source configuration to thread through any more.
    course = Cohort.objects.create(
        slug=f"synthetic-count-cohort-{count}",
        title="Synthetic registration cohort",
        description="Deterministic browser fixture.",
    )
    return RegistrationCampaign.objects.create(
        slug=f"synthetic-count-campaign-{count}",
        title="Synthetic registration campaign",
        edition_label="2026 cohort",
        current_course=course,
        registration_baseline_cohort=course,
        registration_baseline_count=count,
    )


def _assert_no_overflow(page: Page) -> None:
    dimensions = page.evaluate(
        """() => ({
          viewport: document.documentElement.clientWidth,
          document: document.documentElement.scrollWidth,
          content: document.querySelector('main').getBoundingClientRect().right,
        })"""
    )
    assert dimensions["document"] <= dimensions["viewport"], dimensions
    assert dimensions["content"] <= dimensions["viewport"] + 1, dimensions


@pytest.mark.parametrize(("viewport", "viewport_name"), VIEWPORTS)
@pytest.mark.parametrize("count", (0, 1, 7))
def test_copied_registration_count_zero_one_many_light_and_dark(
    page: Page,
    live_server,
    settings,
    viewport: ViewportSize,
    viewport_name: str,
    count: int,
) -> None:
    campaign = _campaign_with_count(settings, count)
    page.set_viewport_size(viewport)
    console_errors: list[str] = []
    failed_requests: list[str] = []
    page.on(
        "console",
        lambda message: console_errors.append(message.text) if message.type == "error" else None,
    )
    page.on("requestfailed", lambda request: failed_requests.append(request.url))
    url = reverse("registration_campaign", kwargs={"campaign_slug": campaign.slug})

    response = page.goto(f"{live_server.url}{url}", wait_until="networkidle")

    assert response is not None and response.status == 200
    expect(page.get_by_role("heading", name=campaign.title)).to_be_visible()
    analytics_choice = page.get_by_role("button", name="Keep analytics off")
    if analytics_choice.is_visible():
        analytics_choice.click()
    if count == 0:
        expect(page.get_by_text("already registered", exact=False)).to_have_count(0)
    else:
        expect(
            page.get_by_text(f"{count} already registered for 2026 cohort", exact=True)
        ).to_be_visible()
    _assert_no_overflow(page)
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(
        path=SCREENSHOTS / f"count-{count}-{viewport_name}-light.png",
        full_page=True,
    )
    # The design system masthead labels the toggle by the mode it switches to, so
    # the control is taken by its stable id rather than the copied shell's
    # "Toggle dark mode" name (as test_course_design_parity already does).
    page.locator("#dark-mode-toggle").click()
    expect(page.locator("body.dark-mode")).to_have_count(1)
    _assert_no_overflow(page)
    page.screenshot(
        path=SCREENSHOTS / f"count-{count}-{viewport_name}-dark.png",
        full_page=True,
    )
    assert console_errors == []
    assert failed_requests == []


def test_copied_registration_count_is_server_rendered_without_javascript(
    browser: Browser,
    live_server,
    settings,
) -> None:
    campaign = _campaign_with_count(settings, 7)
    context = browser.new_context(
        java_script_enabled=False,
        viewport={"width": 390, "height": 844},
    )
    page = context.new_page()
    try:
        url = reverse("registration_campaign", kwargs={"campaign_slug": campaign.slug})
        response = page.goto(f"{live_server.url}{url}", wait_until="load")
        assert response is not None and response.status == 200
        expect(page.get_by_text("7 already registered for 2026 cohort", exact=True)).to_be_visible()
        _assert_no_overflow(page)
    finally:
        context.close()
