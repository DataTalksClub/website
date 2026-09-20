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
        # The prerequisites answer "is this a good fit" as their own
        # opening-group section, ahead of the journey -- not a stage-card
        # fact any more.
        expect(page.locator("#fit-heading")).to_contain_text("Good fit if")
        expect(page.locator(".family-opening-group")).to_contain_text(family.prerequisites)
        expect(page.locator(".family-prerequisites")).to_have_count(0)
        # The journey card is the shared stage-card component
        # (core/_stage_card.html), on its rail variant.
        expect(page.locator(".stage-card-rail")).to_have_count(3)
        # The cards carry no dashed-divider proof block any more -- the same
        # clean shape as the home page's climb cards.
        expect(page.locator(".journey-proof")).to_have_count(0)
        for card in range(3):
            # Kicker and description -- nothing follows the description.
            expect(page.locator(".stage-card-rail").nth(card).locator("p")).to_have_count(2)
        for index, step in enumerate(family.progression):
            expect(page.locator(".stage-card-rail").nth(index)).to_contain_text(step["heading"])
        expect(page.locator(".stage-card-figure")).to_have_count(3)
        expect(page.locator(".family-hero-art")).to_be_visible()
        visible_hero_art = page.locator(".family-hero-art img:visible")
        expect(visible_hero_art).to_have_count(1)
        expected_generic_art = (
            "course-learning-dark.webp" if theme == "dark" else "course-learning.webp"
        )
        assert expected_generic_art in visible_hero_art.get_attribute("src")
        # The family's own scene is drawn once, by the hero: the stages draw
        # the shared journey artwork instead of repeating it.
        expect(page.locator(".stage-card-figure img:visible")).to_have_count(3)
        for index in range(3):
            source = page.locator(".stage-card-figure img:visible").nth(index).get_attribute("src")
            assert expected_generic_art not in source
        # Each disc sits on the dashed rail behind the row, not inside a
        # paragraph, so the three stages read as a sequence.
        expect(page.locator(".stage-card-rail .step-number")).to_have_count(3)
        expect(page.locator(".family-syllabus-row h3")).to_have_count(5)
        expect(page.locator(".family-syllabus-row h3 a")).to_have_count(5)
        expect(page.locator(".family-syllabus-row p")).to_have_count(4)
        value = page.locator(".family-transformation").bounding_box()
        routes = page.locator(".family-register-cohort").bounding_box()
        opening = page.locator(".family-opening-group").bounding_box()
        assert value is not None and routes is not None
        assert opening is not None
        section_gap = value["y"] - (opening["y"] + opening["height"])
        assert 40 <= section_gap <= 72
        assert value["y"] + value["height"] <= routes["y"]
        # The stages carry their own evidence now, so stacked they run longer
        # than three screens; what has to hold is that the visitor *meets* the
        # argument early, which is the section's top, not its bottom.
        assert value["y"] < height if width == 1440 else value["y"] < height * 2
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
