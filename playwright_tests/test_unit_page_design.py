"""The course unit page after the rail/reading-column redesign.

What only a browser can answer here: that the reading column really is capped
at the system's content width instead of stretching to the breakout, that the
rail is an unframed sticky nav rather than a card, that the current row's tint
is drawn without an accent bar, that the mobile breakpoint puts the lesson
first and the rail after it, and that a signed-in reader gets exactly one
read-state control on the page.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from django.test import Client
from django.utils import timezone
from playwright.sync_api import Page, expect

from accounts.models import CustomUser
from courses.models import Cohort, Course, CurriculumFormat, Homework, Module, Unit

pytestmark = [pytest.mark.full, pytest.mark.django_db(transaction=True)]

SCREENSHOTS = Path(".tmp/screenshots/unit-page-redesign")
VIEWPORTS = (
    ({"width": 1440, "height": 900}, "desktop"),
    ({"width": 390, "height": 844}, "mobile"),
)
LESSONS = (
    (10, "01-what-is-ml", "1.1 Introduction to Machine Learning"),
    (20, "02-ml-vs-rules", "1.2 ML vs Rule-Based Systems"),
    (30, "03-supervised", "1.3 Supervised Machine Learning"),
    (40, "06-environment", "Setting up the Environment"),
)


def _shot(page: Page, name: str) -> None:
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOTS / name, full_page=True)


def _settle_analytics_preferences(page: Page) -> None:
    preferences = page.get_by_role("dialog", name="Optional analytics")
    if preferences.is_visible():
        preferences.get_by_role("button", name="Keep analytics off").click()
        expect(preferences).to_be_hidden()


@pytest.fixture
def module_with_lessons() -> tuple[Module, list[Unit]]:
    course = Course.objects.create(slug="ml-zoomcamp-design", title="ML Zoomcamp Design")
    cohort = Cohort.objects.create(
        course=course,
        slug="ml-zoomcamp-design-2026",
        identifier="2026",
        year=2026,
        title="ML Zoomcamp Design 2026",
        description="A module-format cohort used by the unit-page design checks.",
        curriculum_format=CurriculumFormat.MODULES,
        github_repo_url="https://github.com/DataTalksClub/machine-learning-zoomcamp.git",
    )
    homework = Homework.objects.create(
        course=cohort,
        slug="homework-01",
        title="Homework 1: Introduction to Machine Learning",
        due_date=timezone.now() + timedelta(days=7),
    )
    module = Module.objects.create(
        cohort=cohort,
        position=10,
        slug="01-intro",
        title="Introduction to Machine Learning",
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
        for position, slug, title in LESSONS
    ]
    return module, units


def _unit_path(module: Module, unit: Unit) -> str:
    cohort = module.cohort
    return f"/courses/{cohort.course.slug}/{cohort.identifier}/modules/{module.slug}/{unit.slug}"


@pytest.mark.parametrize(("viewport", "suffix"), VIEWPORTS)
def test_the_unit_page_reads_at_a_measure_beside_a_quiet_rail(
    page: Page,
    live_server,
    module_with_lessons: tuple[Module, list[Unit]],
    viewport: dict[str, int],
    suffix: str,
) -> None:
    module, units = module_with_lessons
    page.set_viewport_size(viewport)

    response = page.goto(
        f"{live_server.url}{_unit_path(module, units[1])}", wait_until="networkidle"
    )
    assert response is not None and response.status == 200
    _settle_analytics_preferences(page)

    expect(page.get_by_role("navigation", name="In this module")).to_be_visible()
    expect(page.get_by_role("navigation", name="Unit navigation")).to_be_visible()
    expect(page.get_by_role("navigation", name="Breadcrumb")).to_be_visible()
    expect(page.locator("body")).not_to_contain_text("Traceback")

    # The rail carries no card frame, and no row carries an accent bar.
    rail_chrome = page.locator(".module-rail").evaluate(
        """(node) => {
          const style = getComputedStyle(node);
          return {border: style.borderTopWidth, shadow: style.boxShadow};
        }"""
    )
    assert rail_chrome["shadow"] == "none", rail_chrome
    # An accent bar is a left border with no border on the other three sides;
    # a circular mark's own outline is not one.
    accent_bars = page.evaluate(
        """() => [...document.querySelectorAll('.module-rail, .module-rail *')]
             .filter((node) => {
               const style = getComputedStyle(node);
               const width = (side) => parseFloat(style[`border${side}Width`]) || 0;
               const drawn = (side) => style[`border${side}Style`] !== 'none' && width(side) > 0;
               return drawn('Left') && !drawn('Top') && !drawn('Right') && !drawn('Bottom');
             })
             .map((node) => `${node.tagName.toLowerCase()}.${String(node.className)}`)"""
    )
    assert accent_bars == [], accent_bars

    # No horizontal overflow at either size.
    overflow = page.evaluate(
        """() => ({viewport: document.documentElement.clientWidth,
                   content: document.documentElement.scrollWidth})"""
    )
    assert overflow["content"] <= overflow["viewport"], overflow
    _shot(page, f"unit-signed-out-{suffix}.png")

    if suffix == "desktop":
        widths = page.evaluate(
            """() => {
              const box = (s) => document.querySelector(s).getBoundingClientRect();
              return {main: box('.module-main').width, rail: box('.module-sidebar').width,
                      article: box('.unit-content').width,
                      navRight: box('.unit-navigation').right,
                      mainRight: box('.module-main').right};
            }"""
        )
        # 40.5rem content column, 38rem article inside it, and a rail that
        # weighs less than the column it accompanies.
        assert 640 <= widths["main"] <= 656, widths
        assert 600 <= widths["article"] <= 616, widths
        assert widths["rail"] <= widths["main"], widths
        assert abs(widths["navRight"] - widths["mainRight"]) < 1, widths
    else:
        order = page.evaluate(
            """() => {
              const top = (s) => document.querySelector(s).getBoundingClientRect().top;
              return {main: top('.module-main'), rail: top('.module-sidebar')};
            }"""
        )
        # The phone gets the lesson first and the module list after it.
        assert order["main"] < order["rail"], order


def test_a_signed_in_reader_gets_one_read_control_and_returns_to_the_lesson(
    page: Page,
    live_server,
    module_with_lessons: tuple[Module, list[Unit]],
) -> None:
    module, units = module_with_lessons
    reader = CustomUser.objects.create_user(username="unit-design-reader")
    client = Client()
    client.force_login(reader)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.context.add_cookies(
        [{"name": "sessionid", "value": client.cookies["sessionid"].value, "url": live_server.url}]
    )

    lesson = _unit_path(module, units[1])
    page.goto(f"{live_server.url}{lesson}", wait_until="networkidle")
    _settle_analytics_preferences(page)

    rail = page.locator(".module-rail")
    expect(rail).to_contain_text("0 of 4 read")
    expect(rail.locator("form")).to_have_count(0)

    toggle = page.locator(".unit-footer").get_by_role("button", name="Mark this lesson as read")
    expect(toggle).to_have_count(1)
    expect(page.get_by_role("button", name="Mark this lesson as read")).to_have_count(1)
    _shot(page, "unit-signed-in-desktop.png")

    toggle.click()
    page.wait_for_load_state("networkidle")

    # Back on the lesson that was just finished, not on the module index.
    assert page.url.endswith(lesson)
    expect(page.locator(".module-rail")).to_contain_text("1 of 4 read")
    expect(
        page.locator(".unit-footer").get_by_role("button", name="Mark this lesson as unread")
    ).to_have_count(1)
    _shot(page, "unit-signed-in-read-desktop.png")
