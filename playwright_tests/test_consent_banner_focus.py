"""The undecided consent banner never covers the control keyboard focus is on.

The analytics prompt is a non-modal banner fixed to the foot of the viewport.
While it is open the page reserves its height as the document's bottom scroll
padding, so every control the browser scrolls into view for keyboard focus
stops above the banner instead of behind it (WCAG 2.4.11 Focus Not Obscured).
The page here is a long course landing whose focus stops run well past one
screen, tabbed through from the top to the footer exactly as a keyboard user
would.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from playwright.sync_api import Page, expect

from courses.models import Course, SharedCurriculum, SharedModule
from test_support.course_catalog import make_cohort

pytestmark = [pytest.mark.full, pytest.mark.django_db(transaction=True)]

FOCUS_STATE = """() => {
  const node = document.activeElement;
  if (!node || node === document.body) return null;
  const banner = document.getElementById('analytics-preferences-dialog');
  if (banner.contains(node)) return {inBanner: true};
  const r = node.getBoundingClientRect();
  const b = banner.getBoundingClientRect();
  const overlaps = r.bottom > b.top && r.top < b.bottom && r.right > b.left && r.left < b.right;
  return {
    inBanner: false,
    overlaps,
    label: (node.innerText || node.getAttribute('aria-label') || node.tagName).trim().slice(0, 48),
  };
}"""


def _long_family() -> Course:
    family = Course.objects.create(
        slug="consent-focus-course",
        title="Consent Focus Zoomcamp",
        outcome="Build a long landing page with many keyboard stops.",
        prerequisites="You can use a keyboard.",
    )
    make_cohort(family, 2026, project_count=2)
    curriculum = SharedCurriculum.objects.create(course=family)
    for position in range(12):
        SharedModule.objects.create(
            curriculum=curriculum,
            position=position,
            slug=f"module-{position}",
            title=f"Module {position + 1}: Topic {position + 1}",
            summary=f"Practise topic {position + 1} end to end.",
        )
    return family


@pytest.mark.parametrize(
    "viewport",
    [{"width": 390, "height": 844}, {"width": 1440, "height": 900}],
    ids=["mobile", "desktop"],
)
def test_tabbing_a_long_page_never_lands_behind_the_consent_banner(
    page: Page, live_server, viewport: dict[str, int]
) -> None:
    family = _long_family()
    page.set_viewport_size(viewport)
    page.emulate_media(reduced_motion="reduce")
    page.goto(
        f"{live_server.url}{reverse('course_family', args=[family.slug])}",
        wait_until="networkidle",
    )
    banner = page.get_by_test_id("analytics-preferences-dialog")
    expect(banner).to_be_visible()
    assert not banner.evaluate("element => element.matches(':modal')")
    banner_top = banner.bounding_box()
    assert banner_top is not None and banner_top["y"] > viewport["height"] / 2

    covered: list[str] = []
    stops = 0
    for _ in range(400):
        page.keyboard.press("Tab")
        state = page.evaluate(FOCUS_STATE)
        if state is None or state["inBanner"]:
            # The banner's own controls come last in the tab order.
            break
        stops += 1
        if state["overlaps"]:
            covered.append(state["label"])

    # The walk really did run past one screen into the footer.
    assert stops > 20
    assert page.evaluate("window.scrollY") > viewport["height"]
    assert covered == []


def test_escape_dismisses_the_banner_without_choosing(page: Page, live_server) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{live_server.url}/terms", wait_until="networkidle")
    banner = page.get_by_test_id("analytics-preferences-dialog")
    expect(banner).to_be_visible()
    page.keyboard.press("Escape")
    expect(banner).to_be_hidden()
    assert "dtc_analytics_consent" not in {cookie["name"] for cookie in page.context.cookies()}
    assert not page.evaluate("document.documentElement.classList.contains('has-consent-banner')")


def test_escape_clears_banner_space_synchronously(page: Page, live_server) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{live_server.url}/terms", wait_until="networkidle")
    banner = page.get_by_test_id("analytics-preferences-dialog")
    expect(banner).to_be_visible()
    state = page.evaluate("""() => {
        document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
        const root = document.documentElement;
        return {
            open: document.getElementById('analytics-preferences-dialog').open,
            hasClass: root.classList.contains('has-consent-banner'),
            space: root.style.getPropertyValue('--consent-banner-space'),
        };
    }""")
    assert state == {"open": False, "hasClass": False, "space": ""}
