from pathlib import Path

import pytest
from django.urls import reverse
from playwright.sync_api import Page, expect

from courses.models import Cohort, Course, RegistrationCampaign

pytestmark = [pytest.mark.full, pytest.mark.django_db(transaction=True)]


@pytest.mark.parametrize("width,height", [(1440, 900), (390, 844)])
@pytest.mark.parametrize("surface", ["catalogue", "family"])
def test_course_art_preserves_theme_geometry_and_course_actions(
    page: Page, live_server, width: int, height: int, surface: str
) -> None:
    family = Course.objects.create(
        slug="data-reliability-zoomcamp",
        title="Data Reliability Zoomcamp",
        description="Build reliable data systems through practical projects.",
    )
    cohort = Cohort.objects.create(
        course=family,
        slug="data-reliability-zoomcamp-2026",
        identifier="2026",
        year=2026,
        title="Data Reliability Zoomcamp 2026",
    )
    campaign = RegistrationCampaign.objects.create(
        slug="synthetic-data-reliability",
        title=family.title,
        current_course=cohort,
    )
    path = (
        reverse("course_list")
        if surface == "catalogue"
        else reverse("course_family", kwargs={"course_slug": family.slug})
    )
    page.set_viewport_size({"width": width, "height": height})
    response = page.goto(f"{live_server.url}{path}", wait_until="networkidle")
    assert response is not None and response.status == 200
    art = page.locator(".courses-hero-art, .family-hero-art")
    images = art.locator("img")
    expect(images).to_have_count(2)
    images.evaluate_all("imgs => Promise.all(imgs.map(img => img.decode()))")
    for image in images.all():
        expect(image).to_have_attribute("alt", "")
        assert image.evaluate("img => img.complete && img.naturalWidth === 1024")

    screenshot_dir = Path(".tmp/screenshots/issue-316-engineer")
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    slot_before = art.bounding_box()
    heading_before = page.locator("main h1").bounding_box()
    for theme in ("light", "dark"):
        if theme == "dark":
            page.locator("#dark-mode-toggle").click()
            expect(page.locator("body.dark-mode")).to_have_count(1)
        if width >= 768:
            expect(art.locator("img:visible")).to_have_count(1)
            expect(art.locator(f".doodle-{theme}")).to_be_visible()
            assert art.bounding_box() == slot_before
        else:
            expect(art).to_be_hidden()
        assert page.locator("main h1").bounding_box() == heading_before
        assert page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        )
        if surface == "family":
            registration_url = reverse(
                "registration_campaign", kwargs={"campaign_slug": campaign.slug}
            )
            action = page.locator(f".family-hero-actions a[href='{registration_url}']")
            expect(action).to_be_visible()
            action.focus()
            expect(action).to_be_focused()
        page.screenshot(path=str(screenshot_dir / f"{surface}-{width}-{theme}.png"), full_page=True)
