import re

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
        summary = f"Practice how to {title.lower()} in the course project."
        if position == 0:
            summary = "https://example.com/" + "unbroken-syllabus-summary-url" * 12
        SharedModule.objects.create(
            curriculum=curriculum,
            position=position,
            slug=f"skill-{position}",
            title=title,
            summary=summary,
        )
    learner = User.objects.create_user(username="landing-learner")
    enrollment = Enrollment.objects.create(student=learner, course=cohort)
    submission = ProjectSubmission.objects.create(
        project=cohort.project_set.first(),
        student=learner,
        enrollment=enrollment,
        github_link="https://github.com/example/learner-project",
        # The inline gallery only shows passed work now (owner feedback: the
        # vote/score/pass badge was dropped, so every row shown here must
        # already have passed).
        passed=True,
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
        # The prerequisites answer "who it's for" in their own chapter,
        # after what the course teaches -- not a stage-card fact.
        expect(page.locator("#audience-heading")).to_have_text("Who it's for")
        expect(page.locator(".family-audience")).to_contain_text("Good fit if")
        expect(page.locator(".family-audience")).to_contain_text(family.prerequisites)
        expect(page.locator(".family-prerequisites")).to_have_count(0)
        # The journey card is the shared stage-card component
        # (core/_stage_card.html), in the content-width journey grid.
        journey = page.locator(".journey-grid .stage-card")
        expect(journey).to_have_count(3)
        # The cards carry no dashed-divider proof block any more -- the same
        # clean shape as the home page's climb cards.
        expect(page.locator(".journey-proof")).to_have_count(0)
        for card in range(3):
            # The description is the card's only paragraph.
            expect(journey.nth(card).locator(":scope > p")).to_have_count(1)
        for index, step in enumerate(family.progression):
            expect(journey.nth(index)).to_contain_text(step["heading"])
        expected_generic_art = (
            "course-learning-dark.webp" if theme == "dark" else "course-learning.webp"
        )
        if width >= 768:
            # A phone gets the title, lede and action; wider screens the drawing.
            expect(page.locator(".family-hero-art")).to_be_visible()
            visible_hero_art = page.locator(".family-hero-art img:visible")
            expect(visible_hero_art).to_have_count(1)
            assert expected_generic_art in visible_hero_art.get_attribute("src")
        else:
            expect(page.locator(".family-hero-art")).to_be_hidden()
        # The family's own scene is drawn once, by the hero: the stages draw
        # the shared journey artwork instead of repeating it.
        journey_art = page.locator(".journey-grid .stage-card img:visible")
        expect(journey_art).to_have_count(3)
        for index in range(3):
            source = journey_art.nth(index).get_attribute("src")
            assert expected_generic_art not in source
        # Each stage carries its numbered disc beside its heading, so the
        # three stages read as a sequence.
        expect(page.locator(".journey-grid .stage-card-step .step-number")).to_have_count(3)
        # Four modules and the project, one row component; a summary that is
        # only a link is not drawn as a summary.
        expect(page.locator(".family-syllabus-row h3")).to_have_count(5)
        expect(page.locator(".family-syllabus-row h3 a")).to_have_count(5)
        expect(page.locator(".family-syllabus-row p")).to_have_count(3)
        expect(page.locator(".family-syllabus-project .family-syllabus-marker")).to_have_text(
            "Project"
        )
        expect(page.locator(".family-syllabus-project .family-attempt-dates")).to_contain_text(
            "submit by"
        )
        about = page.locator(".family-about").bounding_box()
        routes = page.locator(".family-register").bounding_box()
        value = page.locator(".family-transformation").bounding_box()
        assert about is not None and routes is not None and value is not None
        # One chapter rhythm: the same seam and spacing between sections.
        section_gap = routes["y"] - (about["y"] + about["height"])
        assert 30 <= section_gap <= 110
        assert routes["y"] + routes["height"] <= value["y"]
        # When and how to join is met early, before the long journey.
        assert routes["y"] < height * 1.5 if width == 1440 else routes["y"] < height * 3
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
        # One goal per page: the hero's single primary action is the open
        # registration itself, the same one the register chapter offers.
        registration_url = reverse("registration_campaign", args=[campaign.slug])
        action = page.locator(".family-hero-actions .cta")
        expect(action).to_have_count(1)
        expect(action).to_have_attribute("href", registration_url)
        page.keyboard.press("Tab")
        action.focus()
        expect(action).to_be_focused()
        assert action.evaluate("el => parseFloat(getComputedStyle(el).outlineWidth)") >= 3
        assert action.bounding_box()["height"] >= 44
        registration = page.locator(f".family-register a[href='{registration_url}']")
        expect(registration).to_be_visible()
        proof = page.locator(".family-proof-more")
        family_gallery_url = f"{reverse('all_projects')}?course={family.slug}"
        expect(proof).to_have_attribute("href", family_gallery_url)
        # "cards like the rest of the cards - entire card clickable" (owner
        # feedback): the repository link's ::after is the whole-card overlay
        # (the same stretched-card-link mechanism the editions cards above
        # already use), rather than just the underlined repository text.
        proof_card = page.locator(".family-proof-card").first
        expect(proof_card).to_have_class(re.compile(r"\bstretched-card-link\b"))
        overlay_position = proof_card.evaluate(
            "el => getComputedStyle("
            "el.querySelector('.family-proof-repository a'), '::after').position"
        )
        assert overlay_position == "absolute"
        # The byline stays its own, separately reachable link above that
        # overlay (the same z-index treatment archive rows give a
        # .person-chip link inside their own whole-card link).
        byline_link = page.locator(".family-proof-byline a").first
        byline_z_index = byline_link.evaluate("el => getComputedStyle(el).zIndex")
        assert byline_z_index not in ("auto", "0")
        expect(byline_link).to_have_attribute(
            "href",
            reverse(
                "cohort_leaderboard_score_breakdown",
                kwargs={
                    "course_slug": family.slug,
                    "cohort_identifier": cohort.identifier,
                    "enrollment_id": enrollment.id,
                },
            ),
        )
        expect(page.locator(".family-proof-repository a").first).to_have_attribute(
            "href", submission.github_link
        )
    proof.click()
    expect(page).to_have_url(f"{live_server.url}{family_gallery_url}")
