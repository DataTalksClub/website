import pytest
from django.urls import reverse
from playwright.sync_api import Page, expect

from courses.models import (
    Course,
    Enrollment,
    ProjectSubmission,
    RegistrationCampaign,
    SharedCurriculum,
    SharedModule,
    User,
)
from test_support.course_catalog import make_cohort

pytestmark = [pytest.mark.full, pytest.mark.django_db(transaction=True)]


@pytest.mark.parametrize("width,height", [(1440, 900), (768, 1024), (390, 844), (320, 740)])
def test_course_value_precedes_route_choice_and_anchors_work(
    page: Page, live_server, width: int, height: int
) -> None:
    family = Course.objects.create(
        slug="landing-review-course",
        title="Practical Prediction Zoomcamp",
        starting_point="You can write Python and want to apply it to a real dataset.",
        prerequisites="You can write Python and use the command line.",
        progression=[
            {
                "heading": "I have a dataset and a question",
                "description": "I want to turn raw information into a useful answer.",
            },
            {
                "heading": "I build and evaluate a model",
                "description": "I prepare data, train a model, and test its predictions.",
            },
            {
                "heading": "I publish a prediction service",
                "description": "I deploy a working project that other people can use.",
            },
        ],
        outcome="Build a prediction service you can test, deploy, and explain.",
        github_repo_url="https://github.com/example/course",
    )
    cohort = make_cohort(family, 2026, project_count=1)
    campaign = RegistrationCampaign.objects.create(
        slug="landing-review-course", title=family.title, current_course=cohort
    )
    curriculum = SharedCurriculum.objects.create(course=family)
    for position, title in enumerate(
        ["Prepare the data", "Train a model", "Evaluate predictions", "Deploy the service"]
    ):
        SharedModule.objects.create(
            curriculum=curriculum,
            position=position,
            slug=f"skill-{position}",
            title=title,
            summary=f"Practice how to {title.lower()} in the course project.",
        )
    learner = User.objects.create_user(username="landing-learner")
    enrollment = Enrollment.objects.create(student=learner, course=cohort)
    ProjectSubmission.objects.create(
        project=cohort.project_set.first(),
        student=learner,
        enrollment=enrollment,
        github_link="https://github.com/example/learner-project",
    )
    page.set_viewport_size({"width": width, "height": height})
    page.emulate_media(reduced_motion="reduce")
    response = page.goto(f"{live_server.url}{reverse('course_family', args=[family.slug])}")
    assert response is not None and response.status == 200
    page.evaluate("document.fonts.ready")
    for theme in ("light", "dark"):
        if theme == "dark":
            page.locator("#dark-mode-toggle").click()
            expect(page.locator("body.dark-mode")).to_have_count(1)
        page.evaluate("scrollTo(0,0)")
        expect(page.locator(".family-lede")).to_have_text(family.outcome)
        expect(page.locator(".family-prerequisites")).to_contain_text(family.prerequisites)
        expect(page.locator(".family-transformation-card")).to_have_count(3)
        for index, step in enumerate(family.progression):
            expect(page.locator(".family-transformation-card").nth(index)).to_contain_text(
                step["heading"]
            )
        expect(page.locator(".family-transformation-figure")).to_have_count(3)
        expect(page.locator(".family-hero-art")).to_be_visible()
        visible_hero_art = page.locator(".family-hero-art img:visible")
        visible_learning_art = page.locator(
            ".family-transformation-figure-course img:visible"
        )
        expect(visible_hero_art).to_have_count(1)
        expect(visible_learning_art).to_have_count(1)
        expected_generic_art = (
            "course-learning-dark.webp" if theme == "dark" else "course-learning.webp"
        )
        assert expected_generic_art in visible_hero_art.get_attribute("src")
        assert expected_generic_art in visible_learning_art.get_attribute("src")
        expect(page.locator(".family-syllabus-row h3")).to_have_count(5)
        expect(page.locator(".family-syllabus-row h3 a")).to_have_count(5)
        expect(page.locator(".family-syllabus-row p")).to_have_count(4)
        value = page.locator(".family-transformation").bounding_box()
        routes = page.locator(".family-register-cohort").bounding_box()
        prerequisites = page.locator(".family-prerequisites").bounding_box()
        assert value is not None and routes is not None
        assert prerequisites is not None
        section_gap = value["y"] - (prerequisites["y"] + prerequisites["height"])
        assert 40 <= section_gap <= 72
        assert value["y"] + value["height"] <= routes["y"]
        assert value["y"] < height if width == 1440 else value["y"] + value["height"] < height * 3
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), page.evaluate(
            """() => [...document.querySelectorAll('main *')].filter(el => {
                const r = el.getBoundingClientRect(); return r.right > innerWidth;
            }).map(el => ({tag: el.tagName, class: el.className,
                          width: el.getBoundingClientRect().width}))"""
        )
        targets = page.locator(
            ".family-hero-actions a, .family-register a, .family-syllabus-intro a, "
            ".family-edition-card h3 a, .family-syllabus-row h3 a, "
            ".family-proof-repository a, .family-proof-byline a, .family-proof > a"
        )
        for index in range(targets.count()):
            box = targets.nth(index).bounding_box()
            assert box is not None and box["height"] >= 44
        action = page.locator(".family-hero-actions a[href='#register-heading']")
        page.keyboard.press("Tab")
        action.focus()
        expect(action).to_be_focused()
        assert action.evaluate("el => parseFloat(getComputedStyle(el).outlineWidth)") >= 3
        assert action.bounding_box()["height"] >= 44
        assert page.locator(".family-hero-actions .band-link").bounding_box()["height"] >= 44
        action.press("Enter")
        assert page.url.endswith("#register-heading")
        expect(page.locator("#register-heading")).to_be_in_viewport()
        registration = page.locator(
            f".family-register a[href='{reverse('registration_campaign', args=[campaign.slug])}']"
        )
        expect(registration).to_be_visible()
        syllabus_action = page.locator(".family-hero-actions a[href='#syllabus-heading']")
        syllabus_action.click()
        expect(page.locator("#syllabus-heading")).to_be_in_viewport()
        proof = page.locator(".family-proof a").first
        expect(proof).to_have_attribute("href", reverse("family_projects", args=[family.slug]))
    proof.click()
    expect(page).to_have_url(f"{live_server.url}{reverse('family_projects', args=[family.slug])}")
