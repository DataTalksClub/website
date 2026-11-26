"""The course rail on a shared module page, in a browser.

What only a browser can answer: that the rail stands beside the syllabus on a
desktop with no fold control, and that a phone and a tablet get a collapsed
"Course contents" fold above the content that opens to the whole course.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from courses.models import Course, SharedCurriculum, SharedLesson, SharedModule

pytestmark = [pytest.mark.core, pytest.mark.django_db(transaction=True)]

SHA = "a" * 40


def _provenance(content_id: str, path: str) -> dict[str, str]:
    return {
        "source_content_id": content_id,
        "source_path": path,
        "source_commit_sha": SHA,
        "source_checksum": "b" * 64,
    }


def _course(lessons_in_first: int) -> str:
    course = Course.objects.create(slug="rail-zoomcamp", title="Rail Zoomcamp")
    curriculum = SharedCurriculum.objects.create(
        course=course,
        parser_version="course-repository-v2",
        **_provenance("11111111-1111-4111-8111-111111111111", "course.yaml"),
    )
    for index in range(2):
        module = SharedModule.objects.create(
            curriculum=curriculum,
            position=index,
            slug=f"0{index + 1}-module",
            title=f"Module {index + 1}: Part {index + 1}",
            **_provenance(f"2222222{index}-2222-4222-8222-222222222222", f"0{index}/module.yaml"),
        )
        for lesson in range(lessons_in_first if index == 0 else 3):
            SharedLesson.objects.create(
                module=module,
                position=lesson,
                slug=f"lesson-{lesson + 1}",
                title=f"Lesson {index + 1}.{lesson + 1}",
                rendered_html="<p>Body</p>",
                **_provenance(
                    f"3333333{index}-33{lesson:02d}-4333-8333-333333333333",
                    f"0{index}/lesson-{lesson}.md",
                ),
            )
    return "/courses/rail-zoomcamp/01-module"


@pytest.fixture
def module_path() -> str:
    return _course(3)


def test_a_long_module_opens_all_its_lessons_on_request(page: Page, live_server) -> None:
    path = _course(16)
    page.set_viewport_size({"width": 1366, "height": 768})
    page.goto(f"{live_server.url}{path}", wait_until="networkidle")

    rail = page.get_by_test_id("course-rail")
    expect(rail.get_by_role("link", name="Lesson 1.16")).to_be_hidden()
    # The rest of the course fits beside the window without scrolling the rail.
    assert rail.evaluate("(node) => node.scrollHeight <= node.clientHeight")
    more = rail.get_by_role("button", name="Show all 16 lessons")
    more.click()
    expect(rail.get_by_role("link", name="Lesson 1.16")).to_be_visible()
    expect(rail.get_by_role("button", name="Show fewer lessons")).to_have_attribute(
        "aria-expanded", "true"
    )


def test_desktop_rail_stands_beside_the_syllabus_without_a_fold(
    page: Page, live_server, module_path: str
) -> None:
    page.set_viewport_size({"width": 1366, "height": 900})
    page.goto(f"{live_server.url}{module_path}", wait_until="networkidle")

    rail = page.get_by_test_id("course-rail")
    expect(rail.locator("summary")).to_be_hidden()
    expect(rail.get_by_role("link", name="Lesson 1.2")).to_be_visible()
    expect(rail.get_by_role("link", name="Module 2: Part 2", exact=False)).to_be_visible()
    rail_box = rail.bounding_box()
    syllabus_box = page.get_by_test_id("module-syllabus").bounding_box()
    assert rail_box is not None and syllabus_box is not None
    assert rail_box["x"] + rail_box["width"] <= syllabus_box["x"]


@pytest.mark.parametrize("width", [390, 768])
def test_phone_and_tablet_fold_the_rail_above_the_content(
    page: Page, live_server, module_path: str, width: int
) -> None:
    page.set_viewport_size({"width": width, "height": 844})
    page.goto(f"{live_server.url}{module_path}", wait_until="networkidle")

    rail = page.get_by_test_id("course-rail")
    summary = rail.locator("summary")
    expect(summary).to_be_visible()
    expect(summary).to_contain_text("Course contents")
    expect(summary).to_contain_text("Module 1: Part 1")
    expect(rail.get_by_role("link", name="Lesson 1.2")).to_be_hidden()
    rail_box = rail.bounding_box()
    syllabus_box = page.get_by_test_id("module-syllabus").bounding_box()
    assert rail_box is not None and syllabus_box is not None
    assert rail_box["y"] + rail_box["height"] <= syllabus_box["y"]

    summary.click()
    expect(rail.get_by_role("link", name="Lesson 1.2")).to_be_visible()
