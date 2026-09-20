from pathlib import Path

import pytest
from django.templatetags.static import static
from django.urls import reverse
from playwright.sync_api import Page, expect

from courses.models import Cohort, Course, RegistrationCampaign
from playwright_tests.accessibility_support import axe_issues

pytestmark = [pytest.mark.full, pytest.mark.django_db(transaction=True)]


@pytest.mark.parametrize("width,height", [(1440, 900), (390, 844)])
def test_course_art_preserves_theme_geometry_and_course_actions(
    page: Page, live_server, width: int, height: int
) -> None:
    """Unknown families keep the generic pair and usable course actions."""
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
    path = reverse("course_family", kwargs={"course_slug": family.slug})
    page.set_viewport_size({"width": width, "height": height})
    response = page.goto(f"{live_server.url}{path}", wait_until="networkidle")
    assert response is not None and response.status == 200
    art = page.locator(".family-hero-art")
    images = art.locator("img")
    expect(images).to_have_count(2)
    images.evaluate_all("imgs => Promise.all(imgs.map(img => img.decode()))")
    for image in images.all():
        expect(image).to_have_attribute("alt", "")
        assert image.evaluate("img => img.complete && img.naturalWidth === 1024")

    screenshot_dir = Path(".tmp/screenshots/issue-401-engineer")
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    slot_before = art.bounding_box()
    heading_before = page.locator("main h1").bounding_box()
    for theme in ("light", "dark"):
        if theme == "dark":
            page.locator("#dark-mode-toggle").click()
            expect(page.locator("body.dark-mode")).to_have_count(1)
        expect(art.locator("img:visible")).to_have_count(1)
        expect(art.locator(f".doodle-{theme}")).to_be_visible()
        assert art.bounding_box() == slot_before
        assert page.locator("main h1").bounding_box() == heading_before
        assert page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        )
        registration_url = reverse("registration_campaign", kwargs={"campaign_slug": campaign.slug})
        action = page.locator(".family-hero-actions a[href='#register-heading']")
        expect(action).to_be_visible()
        action.focus()
        expect(action).to_be_focused()
        expect(page.locator(f".family-register a[href='{registration_url}']")).to_be_visible()
        page.screenshot(path=str(screenshot_dir / f"family-{width}-{theme}.png"), full_page=True)


@pytest.mark.parametrize("width", [1440, 768, 390, 320])
def test_course_index_collage_matches_family_artwork(page: Page, live_server, width: int) -> None:
    """Catalogue captions and existing listing cards use matching course art."""
    family, _ = Course.objects.get_or_create(
        slug="ml-zoomcamp", defaults={"title": "Machine Learning Zoomcamp"}
    )
    Cohort.objects.create(
        course=family,
        slug="illustration-ml-zoomcamp",
        identifier="illustration-test",
        title=family.title,
    )
    page.set_viewport_size({"width": width, "height": 900})
    response = page.goto(f"{live_server.url}{reverse('course_list')}", wait_until="networkidle")
    assert response is not None and response.status == 200
    expect(page.get_by_text("mascot needed", exact=True)).to_have_count(0)
    card = page.locator(".hero-collage-card").filter(has_text=family.title)
    expect(card).to_have_count(1)
    listing = page.locator(".catalog-card").filter(has_text=family.title)
    expect(listing).to_have_count(1)
    listing_link = listing.get_by_role("link", name=family.title, exact=True)
    expect(listing_link).to_have_count(1)
    expect(listing_link).to_have_attribute("href", reverse("course_family", args=[family.slug]))
    expect(listing).to_have_class(
        "card catalog-card interactive-card interactive-lift stretched-card-link"
    )
    expect(listing).not_to_have_attribute("role", "link")
    expect(listing).not_to_have_attribute("tabindex", "0")
    assert listing_link.evaluate(
        """node => {
            const overlay = getComputedStyle(node, '::after');
            return overlay.position === 'absolute'
                && overlay.top === '0px'
                && overlay.right === '0px'
                && overlay.bottom === '0px'
                && overlay.left === '0px';
        }"""
    )
    if width in (1440, 390):
        target_size_issues = [
            issue
            for issue in axe_issues(page, f"course-catalog-{width}")
            if "axe target-size" in issue
        ]
        assert target_size_issues == []
    for slot in (card, listing.locator(".catalog-card-media")):
        slot.locator("img").evaluate_all("imgs => Promise.all(imgs.map(img => img.decode()))")
        light = slot.locator(".doodle-light")
        expect(light).to_have_attribute("src", static("core/illustrations/course-ml-zoomcamp.webp"))
        assert light.evaluate("img => img.complete && img.naturalWidth === 1254")
        expect(light).to_have_attribute("alt", "")
        dark = slot.locator(".doodle-dark")
        expect(dark).to_have_attribute(
            "src", static("core/illustrations/course-ml-zoomcamp-dark.webp")
        )
        assert dark.evaluate("img => img.complete && img.naturalWidth === 1254")
        expect(dark).to_have_attribute("alt", "")
    slot_before = card.bounding_box()
    for theme in ("light", "dark"):
        if theme == "dark":
            page.locator("#dark-mode-toggle").click()
        for slot in (card, listing.locator(".catalog-card-media")):
            expect(slot.locator("img:visible")).to_have_count(1)
            expect(slot.locator(f".doodle-{theme}")).to_be_visible()
        assert card.bounding_box() == slot_before
    expect(page.locator("#courses-hero-heading")).to_be_visible()
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )
    listing_link.focus()
    expect(listing_link).to_be_focused()
