"""The real Studio composition/approval path on desktop and mobile."""

from datetime import timedelta
from pathlib import Path

import pytest
from django.utils import timezone
from playwright.sync_api import expect

from accounts.studio_test_support import make_studio_user
from courses.models import Announcement, Cohort, Course, Homework, Module
from playwright_tests.test_studio_foundation import authenticated_cookie

pytestmark = [pytest.mark.core, pytest.mark.django_db(transaction=True)]


@pytest.mark.parametrize("width", [1440, 390])
def test_template_compose_preview_approve_schedule(page, live_server, width):
    user = make_studio_user(username=f"launch-browser-{width}", roles=("site_admin",))
    authenticated_cookie(page, live_server, user)
    height = 900
    if width == 390:
        height = 844
    page.set_viewport_size({"width": width, "height": height})
    page.goto(live_server.url + "/studio/course-launch/")
    create_browser_course(page, width)
    course = Course.objects.get(slug=f"browser-course-{width}")
    cohort, module = browser_cohort(course, width)
    launch_form = page.locator("form").filter(
        has=page.locator(f"input[name=cohort_id][value='{cohort.pk}']")
    )
    page.reload()
    launch_form.get_by_role("button", name="Generate unarmed schedules").click()
    expect(page.get_by_text("draft", exact=True)).to_be_visible()
    expect(page.get_by_text("Unarmed", exact=True)).to_be_visible()
    capture_state(page, width, "unarmed")
    expect(page.get_by_text("Start Browser module", exact=False)).to_be_visible()
    approve_and_schedule(page)
    assert Announcement.objects.get(cohort=cohort).status == "scheduled"
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    capture_state(page, width, "scheduled")
    compose_browser_draft(page, cohort, module)


def compose_browser_draft(page, cohort, module):
    page.get_by_label("Cohort", exact=True).select_option(str(cohort.pk))
    page.get_by_label("Start owner", exact=True).select_option("module")
    page.get_by_label("Target ID (copy from Starts)", exact=True).fill(str(module.pk))
    page.get_by_role("button", name="Save draft and preview", exact=True).click()
    expect(page.get_by_text("draft", exact=True)).to_be_visible()


def capture_state(page, width, state):
    screenshots = Path(".tmp/screenshots/issue-467")
    screenshots.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=screenshots / f"{state}-{width}.png", full_page=True)


def approve_and_schedule(page):
    page.get_by_role("button", name="Approve current preview", exact=True).click()
    schedule = page.locator("form").filter(
        has=page.get_by_role("button", name="Schedule approved announcement", exact=True)
    )
    schedule.get_by_label("Absolute schedule (ISO 8601 with offset)").fill(
        (timezone.now() + timedelta(days=8)).isoformat()
    )
    schedule.get_by_role("checkbox").check()
    schedule.get_by_role("button", name="Schedule approved announcement", exact=True).click()
    expect(page.get_by_text("scheduled", exact=True)).to_be_visible()


def create_browser_course(page, width):
    page.get_by_label("Name", exact=True).fill("Browser module email")
    page.get_by_label("Email subject", exact=True).fill("{{ module_title }} starts")
    page.get_by_label("Body", exact=True).fill("Start {{ module_title }} at {{ module_url }}")
    page.get_by_role("button", name="Save template draft", exact=True).click()
    page.get_by_text("Browser module email · email · revision 1").click()
    page.get_by_role("button", name="Publish immutable version", exact=True).click()
    page.get_by_label("Course title", exact=True).fill("Browser course")
    page.get_by_label("Course slug", exact=True).fill(f"browser-course-{width}")
    page.get_by_role("button", name="Create course", exact=True).click()


def browser_cohort(course, width):
    start = timezone.now() + timedelta(days=7)
    cohort = Cohort.objects.create(
        course=course,
        slug=f"browser-cohort-{width}",
        identifier="2027",
        title="Browser cohort",
        curriculum_format="modules",
    )
    homework = Homework.objects.create(
        course=cohort, slug="hw1", title="Homework", due_date=start + timedelta(days=14)
    )
    module = Module.objects.create(
        cohort=cohort,
        position=1,
        slug="one",
        title="Browser module",
        terminal_homework=homework,
        starts_at=start,
    )
    return cohort, module
