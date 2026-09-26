"""A learner saves one question at a time and submits only from Review."""

from pathlib import Path

import pytest
from community_base.homework_steps.models import HomeworkDraft
from django.conf import settings
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import Page, expect

from accounts.models import User
from courses.models import Cohort, Homework, HomeworkState, Question, Submission
from courses.views.homework_steps import assignment_key

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
    route_kwargs = {
        "course_slug": cohort.course.slug,
        "cohort_identifier": cohort.identifier,
        "homework_slug": homework.slug,
    }
    path = reverse("cohort_homework", kwargs=route_kwargs)
    review_path = reverse(
        "cohort_homework_step",
        kwargs={**route_kwargs, "homework_step": "review"},
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

    page.route(f"**/homework/{homework.slug}/**", fail_saves)
    page.get_by_label("Your answer").fill("Because it comes first")
    save_status = page.locator("[data-save-status]")
    expect(save_status).to_have_attribute("role", "alert")
    expect(save_status).to_contain_text("could not be saved")
    page.get_by_role("link", name="Review & submit").click()
    expect(page.get_by_text("Explain your choice", exact=True)).to_be_visible()
    expect(page.get_by_label("Your answer")).to_have_value("Because it comes first")
    page.unroute(f"**/homework/{homework.slug}/**", fail_saves)
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
    expect(page).to_have_url(f"{live_server.url}{review_path}")
    assert Submission.objects.filter(student=member, homework=homework).count() == 1


def test_required_homework_url_allows_draft_save_but_blocks_blank_submit(page: Page, live_server):
    cohort = Cohort.objects.create(slug="required-url-course", title="Required URL Course")
    homework = Homework.objects.create(
        course=cohort,
        title="Homework with a required URL",
        due_date=timezone.now() + timezone.timedelta(days=7),
        state=HomeworkState.OPEN.value,
        slug="required-url-homework",
        homework_url_field=True,
        learning_in_public_cap=0,
        time_spent_lectures_field=False,
        time_spent_homework_field=False,
        faq_contribution_field=False,
    )
    Question.objects.create(homework=homework, text="Explain your work", question_type="FF")
    member = User.objects.create_user(
        username="required-url-member@example.invalid",
        email="required-url-member@example.invalid",
        password="required-url-pass",
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
    route_kwargs = {
        "course_slug": cohort.course.slug,
        "cohort_identifier": cohort.identifier,
        "homework_slug": homework.slug,
    }
    start_path = reverse("cohort_homework", kwargs=route_kwargs)
    review_path = reverse(
        "cohort_homework_step",
        kwargs={**route_kwargs, "homework_step": "review"},
    )
    page.goto(f"{live_server.url}{start_path}")
    page.get_by_role("button", name="Keep analytics off").click()
    page.get_by_role("link", name="Start questions").click()
    page.get_by_role("button", name="Save & continue").click()
    expect(page.get_by_role("heading", name="Review & submit")).to_be_visible()

    homework_url = page.locator('input[name="final_homework_url"]')
    draft = HomeworkDraft.objects.get(user=member, assignment_key=assignment_key(homework))
    revision_before_save = draft.revision
    page.get_by_role("button", name="Save draft").click()
    expect(page.get_by_role("heading", name="Review & submit")).to_be_visible()
    draft.refresh_from_db()
    assert draft.revision > revision_before_save
    assert draft.final_fields.get("homework_url", "") == ""
    assert not Submission.objects.filter(student=member, homework=homework).exists()

    revision_after_save = draft.revision
    page.get_by_role("button", name="Submit homework").click()
    expect(page).to_have_url(f"{live_server.url}{review_path}")
    expect(homework_url).to_be_focused()
    assert homework_url.evaluate("field => field.validity.valueMissing")
    draft.refresh_from_db()
    assert draft.revision == revision_after_save
    assert not Submission.objects.filter(student=member, homework=homework).exists()

    homework_url.fill("https://github.com/example/project")
    page.get_by_role("button", name="Submit homework").click()
    expect(page).to_have_url(f"{live_server.url}{review_path}")
    expect(page.get_by_text("Your homework was submitted.", exact=True)).to_be_visible()
    submission = Submission.objects.get(student=member, homework=homework)
    assert submission.homework_link == "https://github.com/example/project"
