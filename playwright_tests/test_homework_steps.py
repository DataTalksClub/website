"""A learner saves one question at a time and submits only from Review."""

from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import Page, expect

from accounts.models import User
from courses.models import Cohort, Homework, HomeworkState, Question, Submission

pytestmark = [pytest.mark.core, pytest.mark.django_db(transaction=True)]


def test_homework_steps_save_resume_and_submit_at_desktop_and_mobile(page: Page, live_server):
    cohort = Cohort.objects.create(slug="stepper-course", title="Stepper Course")
    homework = Homework.objects.create(
        course=cohort,
        title="Two question homework",
        description="Work through each question, then review your answers.",
        due_date=timezone.now() + timezone.timedelta(days=7),
        state=HomeworkState.OPEN.value,
        slug="stepper-homework",
        homework_url_field=False,
        learning_in_public_cap=0,
        time_spent_lectures_field=False,
        time_spent_homework_field=False,
        faq_contribution_field=False,
    )
    Question.objects.create(
        homework=homework,
        text="Choose a letter",
        question_type="MC",
        possible_answers="Alpha\nBeta",
        correct_answer="1",
    )
    Question.objects.create(
        homework=homework,
        text="Explain your choice",
        question_type="FF",
    )
    member = User.objects.create_user(
        username="stepper-member@example.invalid",
        email="stepper-member@example.invalid",
        password="stepper-pass",
    )
    client = Client()
    client.force_login(member)
    page.context.add_cookies(
        [
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                "url": live_server.url,
            }
        ]
    )
    path = reverse(
        "cohort_homework",
        kwargs={
            "course_slug": cohort.course.slug,
            "cohort_identifier": cohort.identifier,
            "homework_slug": homework.slug,
        },
    )
    page.goto(f"{live_server.url}{path}")
    page.get_by_role("button", name="Keep analytics off").click()
    expect(page.get_by_role("heading", name="Introduction")).to_be_visible()
    page.get_by_role("link", name="Start questions").click()
    expect(page.get_by_text("Choose a letter", exact=True)).to_be_visible()
    page.get_by_label("Alpha").check()
    page.get_by_role("button", name="Save & continue").click()
    expect(page.get_by_text("Explain your choice", exact=True)).to_be_visible()
    page.wait_for_load_state("load")

    def fail_saves(route):
        if route.request.method == "POST":
            route.fulfill(status=200, content_type="application/json", body='{"saved":false}')
        else:
            route.continue_()

    page.route("**/homework/stepper-homework*", fail_saves)
    page.get_by_label("Your answer").fill("Because it comes first")
    expect(page.locator("[data-save-status]")).to_contain_text("Save failed")
    page.get_by_role("link", name="Review & submit").click()
    expect(page.get_by_text("Explain your choice", exact=True)).to_be_visible()
    expect(page.get_by_label("Your answer")).to_have_value("Because it comes first")
    page.unroute("**/homework/stepper-homework*", fail_saves)
    page.get_by_role("button", name="Save & continue").click()
    expect(page.get_by_role("heading", name="Review & submit")).to_be_visible()
    expect(page.locator(".homework-review-list")).to_contain_text("Alpha")
    expect(page.locator(".homework-review-list")).to_contain_text("Because it comes first")
    assert not Submission.objects.filter(student=member, homework=homework).exists()

    screenshot_dir = Path(settings.BASE_DIR) / ".tmp" / "screenshots"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(screenshot_dir / "homework-steps-desktop.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    expect(page.get_by_role("heading", name="Review & submit")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(screenshot_dir / "homework-steps-mobile.png"), full_page=True)

    page.reload()
    expect(page.locator(".homework-review-list")).to_contain_text("Because it comes first")
    page.get_by_role("button", name="Submit homework").click()
    expect(page).to_have_url(f"{live_server.url}{path}")
    assert Submission.objects.filter(student=member, homework=homework).count() == 1
