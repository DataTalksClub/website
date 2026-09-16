from datetime import timedelta
from pathlib import Path

import pytest
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import Page, expect

from courses.models import Cohort, Course, Enrollment, Project, ProjectSubmission, User
from playwright_tests.accessibility_support import axe_issues

pytestmark = [pytest.mark.full, pytest.mark.django_db(transaction=True)]
EVIDENCE = Path(__file__).resolve().parents[1] / ".tmp/project-gallery-20260916/browser"


@pytest.fixture
def project_gallery_data():
    first = Course.objects.create(
        slug="gallery-engineering",
        title="Practical Data Engineering, Analytics and Reliable Production Pipelines Zoomcamp",
    )
    second = Course.objects.create(slug="gallery-models", title="Models Zoomcamp")
    rows = []
    for index, (family, year, repository) in enumerate(
        (
            (first, 2025, "https://github.com/example/" + "long-project-" * 9 + "capstone#readme"),
            (first, 2025, "https://github.com/example/needle-pipeline"),
            (second, 2024, ""),
        )
    ):
        cohort, _ = Cohort.objects.get_or_create(
            course=family,
            identifier=f"fall-{year}",
            defaults={"slug": f"{family.slug}-{year}", "year": year, "title": family.title},
        )
        project, _ = Project.objects.get_or_create(
            course=cohort,
            slug="capstone",
            defaults={
                "title": "Capstone: " + "Documenting and deploying a practical pipeline " * 3,
                "submission_due_date": timezone.now() + timedelta(days=1),
                "peer_review_due_date": timezone.now() + timedelta(days=2),
            },
        )
        learner = User.objects.create_user(username=f"gallery-learner-{index}")
        enrollment = Enrollment.objects.create(
            student=learner,
            course=cohort,
            display_name="LearnerWithAnExceptionallyLongUnbrokenPublicDisplayName"
            if index == 0
            else f"Gallery Learner {index}",
        )
        rows.append(
            ProjectSubmission.objects.create(
                project=project,
                student=learner,
                enrollment=enrollment,
                github_link=repository,
                passed=True,
            )
        )
    return first, second, rows


@pytest.mark.parametrize("width", [1440, 390, 320])
def test_project_discovery_works_with_long_and_sparse_rows_in_both_themes(
    page: Page, live_server, project_gallery_data, width: int
) -> None:
    family, _, rows = project_gallery_data
    page.set_viewport_size({"width": width, "height": 900 if width == 1440 else 844})
    page.emulate_media(reduced_motion="reduce")
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    gallery_url = f"{live_server.url}{reverse('all_projects')}"
    for theme in ("light", "dark"):
        page.goto(gallery_url)
        if theme == "dark":
            page.locator("#dark-mode-toggle").click()
        page.evaluate("document.fonts.ready")
        expect(page.locator("body.dark-mode")).to_have_count(int(theme == "dark"))
        expect(page.locator(".gallery-submission-row")).to_have_count(3)
        expect(page.locator(".gallery-submission-row").last).to_contain_text(
            "Repository link unavailable"
        )
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert page.get_by_label("Sort by", exact=True).evaluate("""select => {
            const style = getComputedStyle(select);
            const context = document.createElement('canvas').getContext('2d');
            context.font = style.font;
            const available = select.clientWidth
                - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight) - 16;
            return Array.from(select.options).every(option =>
                context.measureText(option.text).width <= available);
        }""")
        for target in page.locator(
            ".gallery-filters input, .gallery-filters select, "
            ".gallery-filters a, .gallery-submission-row a"
        ).all():
            box = target.bounding_box()
            assert box is not None and box["height"] >= 44
        assert axe_issues(page, f"project-gallery-{width}-{theme}") == []
        page.screenshot(path=str(EVIDENCE / f"representative-{width}-{theme}.png"), full_page=True)

        query = page.get_by_label("Repository or assignment", exact=True)
        query.focus()
        page.keyboard.press("Tab")
        course = page.get_by_label("Course", exact=True)
        expect(course).to_be_focused()
        assert course.evaluate("el => parseFloat(getComputedStyle(el).outlineWidth)") >= 3
        course.select_option(family.slug)
        page.get_by_label("Year", exact=True).select_option("2025")
        query.fill("needle")
        query.press("Enter")
        expect(page.locator(".gallery-submission-row")).to_have_count(1)
        expect(page.get_by_label("Course", exact=True)).to_have_value(family.slug)
        expect(page.get_by_label("Year", exact=True)).to_have_value("2025")
        repository = page.locator(".gallery-cell-repository a")
        expect(repository).to_have_attribute("href", rows[1].github_link)
        expect(repository).to_have_attribute("target", "_blank")
        expect(repository).to_have_attribute("rel", "noopener noreferrer")
        # The "View cohort ->" link was removed from each row (owner
        # feedback: "view cohort - remove"); the cohort is already named as
        # plain text in the row's course/cohort line instead.
        expect(page.locator(".gallery-submission-row")).not_to_contain_text("View cohort")

        page.get_by_label("Repository or assignment", exact=True).fill("no-matching-project")
        page.get_by_role("button", name="Find projects", exact=True).click()
        expect(
            page.get_by_role("heading", name="No submissions match these filters")
        ).to_be_visible()
        assert axe_issues(page, f"project-gallery-empty-{width}-{theme}") == []
        page.get_by_role("link", name="Clear filters", exact=True).first.click()
        expect(page.locator(".gallery-submission-row")).to_have_count(3)
        expect(page.get_by_label("Repository or assignment", exact=True)).to_have_value("")


@pytest.mark.parametrize("width", [1440, 390, 320])
def test_gallery_invalid_filter_focus_and_targets_in_both_themes(
    page: Page, live_server, project_gallery_data, width: int
) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    page.set_viewport_size({"width": width, "height": 900 if width == 1440 else 844})
    gallery_url = f"{live_server.url}{reverse('all_projects')}"
    for theme in ("light", "dark"):
        page.goto(gallery_url + "?year=unknown")
        expect(page.locator(".a11y-error-summary")).to_be_focused()
        if theme == "dark":
            page.locator("#dark-mode-toggle").click()
        expect(page.get_by_label("Year", exact=True)).to_have_attribute("aria-invalid", "true")
        error_link = page.locator('.a11y-error-summary a[href="#id_year"]')
        box = error_link.bounding_box()
        assert box is not None and box["height"] >= 44
        error_link.click()
        expect(page.get_by_label("Year", exact=True)).to_be_focused()
        assert axe_issues(page, f"project-gallery-invalid-{width}-{theme}") == []
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.evaluate("scrollTo(0, 0)")
        page.screenshot(path=str(EVIDENCE / f"invalid-{width}-{theme}.png"), full_page=True)


@pytest.mark.parametrize("width", [1440, 390, 320])
def test_gallery_many_row_pagination_in_both_themes(
    page: Page, live_server, project_gallery_data, width: int
) -> None:
    family, _, rows = project_gallery_data
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    page.set_viewport_size({"width": width, "height": 900 if width == 1440 else 844})
    page.emulate_media(reduced_motion="reduce")
    for index in range(49):
        learner = User.objects.create_user(username=f"gallery-page-{index}")
        enrollment = Enrollment.objects.create(student=learner, course=rows[0].project.course)
        ProjectSubmission.objects.create(
            project=rows[0].project,
            student=learner,
            enrollment=enrollment,
            github_link=f"https://github.com/example/paged-{index}",
            passed=True,
        )
    gallery_url = f"{live_server.url}{reverse('all_projects')}"
    query = f"course={family.slug}&year=2025&sort=recent"
    for theme in ("light", "dark"):
        page.goto(gallery_url + "?" + query)
        if theme == "dark":
            page.locator("#dark-mode-toggle").click()
        expect(page.locator("body.dark-mode")).to_have_count(int(theme == "dark"))
        for number, count in ((1, 25), (2, 25), (3, 1)):
            expect(page.locator(".gallery-submission-row")).to_have_count(count)
            expect(page.get_by_label("Sort by", exact=True)).to_have_value("recent")
            navigation = page.get_by_role("navigation", name="Project submission pages")
            expect(navigation.locator('[aria-current="page"]')).to_have_text(str(number))
            for direction, disabled in (("Previous page", number == 1), ("Next page", number == 3)):
                control = navigation.get_by_role("link", name=direction, exact=True)
                if disabled:
                    expect(control).to_have_attribute("aria-disabled", "true")
                    assert control.get_attribute("href") is None
                    assert control.evaluate("el => el.tabIndex") == -1
                else:
                    expect(control).not_to_have_attribute("aria-disabled", "true")
            for target in navigation.locator("a").all():
                box = target.bounding_box()
                assert box is not None and box["height"] >= 44 and box["width"] >= 44
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert axe_issues(page, f"project-gallery-page-{number}-{width}-{theme}") == []
            navigation.scroll_into_view_if_needed()
            page.screenshot(path=str(EVIDENCE / f"pagination-{number}-{width}-{theme}.png"))
            if number < 3:
                navigation.get_by_role("link", name="Next page", exact=True).click()
                expect(page).to_have_url(gallery_url + f"?page={number + 1}&" + query)
        page.get_by_role("link", name="Previous page", exact=True).click()
        expect(page).to_have_url(gallery_url + "?page=2&" + query)


def test_gallery_without_any_submissions_has_a_useful_empty_state(page: Page, live_server):
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    page.set_viewport_size({"width": 320, "height": 844})
    page.goto(f"{live_server.url}{reverse('all_projects')}")
    expect(page.get_by_role("heading", name="No project submissions yet")).to_be_visible()
    expect(page.get_by_role("link", name="Explore courses", exact=True)).to_have_attribute(
        "href", reverse("course_list")
    )
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert axe_issues(page, "project-gallery-no-submissions") == []
    page.screenshot(path=str(EVIDENCE / "no-submissions-320-light.png"), full_page=True)
