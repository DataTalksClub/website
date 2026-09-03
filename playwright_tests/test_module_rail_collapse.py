"""Hiding and restoring the module rail on a lesson page.

What only a browser can answer: that the control really removes the rail from
the accessibility tree rather than pushing it offscreen, that the preference
survives a move to the next lesson without the rail flashing into view first,
that the reading measure is identical in both states, that keyboard focus is
handed from each half of the control to the other, and that the whole affordance
stays off the phone, where the rail is already stacked under the lesson.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from django.utils import timezone
from playwright.sync_api import Page, expect

from courses.models import Cohort, Course, CurriculumFormat, Homework, Module, Unit
from playwright_tests.accessibility_support import assert_accessible_page

pytestmark = [pytest.mark.core, pytest.mark.django_db(transaction=True)]

SCREENSHOTS = Path(".tmp/screenshots/module-rail-collapse")
DESKTOP = {"width": 1440, "height": 900}
MOBILE = {"width": 390, "height": 844}

# Records the rail's computed display the instant it is inserted into the
# document, which is the only moment a pre-paint bootstrap could be beaten by a
# script that runs at the foot of the body.
FIRST_DISPLAY_PROBE = """
window.__railFirstDisplay = 'not-inserted';
var observer = new MutationObserver(function (records, self) {
  var rail = document.getElementById('module-rail');
  if (rail) {
    window.__railFirstDisplay = getComputedStyle(rail).display;
    self.disconnect();
  }
});
observer.observe(document, {childList: true, subtree: true});
"""


def _shot(page: Page, name: str) -> None:
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOTS / name, full_page=True)


def _settle_analytics_preferences(page: Page) -> None:
    preferences = page.get_by_role("dialog", name="Optional analytics")
    if preferences.is_visible():
        preferences.get_by_role("button", name="Keep analytics off").click()
        expect(preferences).to_be_hidden()


@pytest.fixture
def lessons() -> tuple[Module, list[Unit]]:
    course = Course.objects.create(slug="llm-zoomcamp-rail", title="LLM Zoomcamp Rail")
    cohort = Cohort.objects.create(
        course=course,
        slug="llm-zoomcamp-rail-2026",
        identifier="2026",
        year=2026,
        title="LLM Zoomcamp Rail 2026",
        description="A module-format cohort used by the rail hide/show checks.",
        curriculum_format=CurriculumFormat.MODULES,
        github_repo_url="https://github.com/DataTalksClub/llm-zoomcamp.git",
    )
    homework = Homework.objects.create(
        course=cohort,
        slug="homework-01",
        title="Homework 1: Agentic RAG",
        due_date=timezone.now() + timedelta(days=7),
    )
    module = Module.objects.create(
        cohort=cohort,
        position=10,
        slug="01-agentic-rag",
        title="Agentic RAG",
        terminal_homework=homework,
    )
    units = [
        Unit.objects.create(
            module=module,
            position=position,
            slug=slug,
            title=title,
            content_markdown="A paragraph of the lesson body.\n\n" * 12,
        )
        for position, slug, title in (
            (10, "01-intro", "1.1 Introduction"),
            (20, "02-search", "1.2 Search"),
            (30, "03-evaluation", "1.3 Evaluation"),
        )
    ]
    return module, units


def _path(module: Module, unit: Unit) -> str:
    cohort = module.cohort
    return f"/courses/{cohort.course.slug}/{cohort.identifier}/modules/{module.slug}/{unit.slug}"


def _measure(page: Page) -> dict[str, float]:
    return page.evaluate(
        """() => {
          const box = (s) => document.querySelector(s).getBoundingClientRect();
          const article = box('.unit-content');
          return {
            width: article.width,
            centre: article.x + article.width / 2,
            viewportCentre: document.documentElement.clientWidth / 2,
          };
        }"""
    )


def test_hiding_the_rail_removes_it_and_keeps_the_reading_measure(
    page: Page, live_server, lessons: tuple[Module, list[Unit]]
) -> None:
    module, units = lessons
    page.set_viewport_size(DESKTOP)
    page.goto(f"{live_server.url}{_path(module, units[1])}", wait_until="networkidle")
    _settle_analytics_preferences(page)

    rail = page.get_by_role("navigation", name="In this module")
    collapse = page.get_by_role("button", name="Hide the module lessons")
    restore = page.get_by_role("button", name="Show the module lessons")

    # Expanded is the default for a reader who has never used the control.
    assert page.evaluate("document.documentElement.dataset.moduleRail") == "expanded"
    expect(rail).to_be_visible()
    expect(collapse).to_be_visible()
    expect(restore).to_be_hidden()
    expect(collapse).to_have_attribute("aria-expanded", "true")
    expect(collapse).to_have_attribute("aria-controls", "module-rail")
    expanded_measure = _measure(page)
    _shot(page, "expanded-desktop.png")

    collapse.click()

    assert page.evaluate("document.documentElement.dataset.moduleRail") == "collapsed"
    assert page.evaluate("localStorage.getItem('moduleRailCollapsed')") == "true"
    # Gone from the accessibility tree, not merely out of sight: Playwright's
    # role engine reads the same tree a screen reader does.
    expect(page.get_by_role("navigation", name="In this module")).to_have_count(0)
    assert page.evaluate("document.getElementById('module-rail').checkVisibility()") is False
    assert (
        page.evaluate("getComputedStyle(document.getElementById('module-rail')).display") == "none"
    )
    # No lesson link of the hidden rail is left in the tab order.
    assert (
        page.evaluate(
            """() => [...document.querySelectorAll('#module-rail a')]
                 .filter((node) => node.checkVisibility()).length"""
        )
        == 0
    )

    expect(restore).to_be_visible()
    expect(restore).to_have_attribute("aria-expanded", "false")
    expect(restore).to_have_attribute("aria-controls", "module-rail")
    expect(restore).to_be_focused()

    # The prose never reflows: the reclaimed room re-centres the column instead
    # of stretching it past the measure the page was designed around.
    collapsed_measure = _measure(page)
    assert abs(collapsed_measure["width"] - expanded_measure["width"]) <= 1, (
        expanded_measure,
        collapsed_measure,
    )
    expanded_offset = abs(expanded_measure["centre"] - expanded_measure["viewportCentre"])
    collapsed_offset = abs(collapsed_measure["centre"] - collapsed_measure["viewportCentre"])
    assert collapsed_offset < expanded_offset, (expanded_measure, collapsed_measure)
    _shot(page, "collapsed-desktop.png")

    # The restore stub is a real 24x24+ target and it is the rail's own place.
    stub = restore.bounding_box()
    assert stub is not None
    assert stub["width"] >= 24 and stub["height"] >= 24, stub
    assert stub["x"] + stub["width"] <= collapsed_measure["centre"], stub

    restore.click()
    assert page.evaluate("document.documentElement.dataset.moduleRail") == "expanded"
    assert page.evaluate("localStorage.getItem('moduleRailCollapsed')") == "false"
    expect(page.get_by_role("navigation", name="In this module")).to_be_visible()
    expect(collapse).to_be_focused()


def test_the_preference_follows_the_reader_without_a_flash(
    page: Page, live_server, lessons: tuple[Module, list[Unit]]
) -> None:
    module, units = lessons
    page.set_viewport_size(DESKTOP)
    page.add_init_script(FIRST_DISPLAY_PROBE)
    page.goto(f"{live_server.url}{_path(module, units[0])}", wait_until="networkidle")
    _settle_analytics_preferences(page)

    assert page.evaluate("window.__railFirstDisplay") == "grid"
    page.get_by_role("button", name="Hide the module lessons").click()

    # The next lesson is a fresh document, so the stored preference has to be
    # applied by the head bootstrap before the rail element exists at all.
    page.goto(f"{live_server.url}{_path(module, units[1])}", wait_until="networkidle")
    assert page.evaluate("document.documentElement.dataset.moduleRail") == "collapsed"
    assert page.evaluate("window.__railFirstDisplay") == "none"
    expect(page.get_by_role("navigation", name="In this module")).to_have_count(0)
    expect(page.get_by_role("button", name="Show the module lessons")).to_be_visible()

    page.reload(wait_until="networkidle")
    assert page.evaluate("window.__railFirstDisplay") == "none"
    expect(page.get_by_role("navigation", name="In this module")).to_have_count(0)


def test_the_control_is_keyboard_operable_in_both_directions(
    page: Page, live_server, lessons: tuple[Module, list[Unit]]
) -> None:
    module, units = lessons
    page.set_viewport_size(DESKTOP)
    page.goto(f"{live_server.url}{_path(module, units[1])}", wait_until="networkidle")
    _settle_analytics_preferences(page)

    collapse = page.get_by_role("button", name="Hide the module lessons")
    restore = page.get_by_role("button", name="Show the module lessons")

    # Arrive on the control by keyboard, so the ring under test is the one a
    # keyboard reader gets rather than the click-focus state.
    collapse.focus()
    page.keyboard.press("Shift+Tab")
    page.keyboard.press("Tab")
    expect(collapse).to_be_focused()
    focus_ring = page.evaluate(
        """() => {
          const style = getComputedStyle(document.activeElement);
          return {width: style.outlineWidth, style: style.outlineStyle};
        }"""
    )
    assert float(focus_ring["width"].replace("px", "")) >= 2, focus_ring
    assert focus_ring["style"] != "none", focus_ring

    page.keyboard.press("Enter")
    expect(restore).to_be_focused()
    assert page.evaluate("document.documentElement.dataset.moduleRail") == "collapsed"

    page.keyboard.press(" ")
    expect(collapse).to_be_focused()
    assert page.evaluate("document.documentElement.dataset.moduleRail") == "expanded"


def test_the_phone_keeps_the_rail_and_never_shows_the_control(
    page: Page, live_server, lessons: tuple[Module, list[Unit]]
) -> None:
    module, units = lessons
    page.set_viewport_size(MOBILE)
    page.goto(f"{live_server.url}{_path(module, units[1])}", wait_until="networkidle")
    _settle_analytics_preferences(page)

    expect(page.get_by_role("button", name="Hide the module lessons")).to_be_hidden()
    expect(page.get_by_role("navigation", name="In this module")).to_be_visible()
    _shot(page, "expanded-mobile.png")

    # A reader who hid the rail on a laptop still gets it on their phone: the
    # stored preference is honoured only where hiding reclaims reading room.
    page.evaluate("localStorage.setItem('moduleRailCollapsed', 'true')")
    page.reload(wait_until="networkidle")
    _settle_analytics_preferences(page)

    assert page.evaluate("document.documentElement.dataset.moduleRail") == "collapsed"
    expect(page.get_by_role("navigation", name="In this module")).to_be_visible()
    expect(page.get_by_role("button", name="Hide the module lessons")).to_be_hidden()
    expect(page.get_by_role("button", name="Show the module lessons")).to_be_hidden()
    order = page.evaluate(
        """() => {
          const top = (s) => document.querySelector(s).getBoundingClientRect().top;
          return {main: top('.module-main'), rail: top('.module-sidebar')};
        }"""
    )
    assert order["main"] < order["rail"], order
    _shot(page, "collapsed-preference-mobile.png")

    overflow = page.evaluate(
        """() => ({viewport: document.documentElement.clientWidth,
                   content: document.documentElement.scrollWidth})"""
    )
    assert overflow["content"] <= overflow["viewport"], overflow


@pytest.mark.parametrize("dark", [False, True])
def test_the_control_clears_contrast_in_both_themes(
    page: Page, live_server, lessons: tuple[Module, list[Unit]], dark: bool
) -> None:
    module, units = lessons
    page.set_viewport_size(DESKTOP)
    page.goto(f"{live_server.url}{_path(module, units[1])}", wait_until="networkidle")
    _settle_analytics_preferences(page)
    if dark:
        page.evaluate("localStorage.setItem('darkMode', 'true')")
        page.reload(wait_until="networkidle")
        _settle_analytics_preferences(page)
        assert page.evaluate("document.body.dataset.darkMode") == "true"

    ratios = page.evaluate(
        """() => {
          const channel = (value) => {
            const c = value / 255;
            return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
          };
          const luminance = (colour) => {
            const [r, g, b] = colour.match(/[0-9.]+/g).map(Number);
            return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
          };
          const ratio = (a, b) => {
            const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p);
            return (x + 0.05) / (y + 0.05);
          };
          const node = document.getElementById('module-rail-collapse');
          const style = getComputedStyle(node);
          return {
            glyph: ratio(style.color, style.backgroundColor),
            outline: ratio(style.borderTopColor, style.backgroundColor),
          };
        }"""
    )
    # The glyph is text-equivalent content and the 2px outline is the control's
    # boundary; both are held to the 3:1 non-text floor and the glyph to 4.5:1.
    assert ratios["glyph"] >= 4.5, ratios
    assert ratios["outline"] >= 3, ratios
    _shot(page, f"expanded-{'dark' if dark else 'light'}-desktop.png")

    page.get_by_role("button", name="Hide the module lessons").click()
    _shot(page, f"collapsed-{'dark' if dark else 'light'}-desktop.png")


@pytest.mark.parametrize("collapsed", [False, True])
def test_the_lesson_page_is_accessible_in_both_rail_states(
    page: Page, live_server, lessons: tuple[Module, list[Unit]], collapsed: bool
) -> None:
    module, units = lessons
    page.set_viewport_size(DESKTOP)
    page.goto(f"{live_server.url}{_path(module, units[1])}", wait_until="networkidle")
    _settle_analytics_preferences(page)
    if collapsed:
        page.get_by_role("button", name="Hide the module lessons").click()

    page.wait_for_load_state("load")
    assert_accessible_page(
        page,
        f"unit.rail-{'collapsed' if collapsed else 'expanded'}",
        comprehensive=True,
    )
