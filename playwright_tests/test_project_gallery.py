from datetime import timedelta
from pathlib import Path

import pytest
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import Page, expect

from courses.models import Cohort, Course, Enrollment, Project, ProjectSubmission, User
from playwright_tests.accessibility_support import axe_issues

pytestmark = [pytest.mark.full, pytest.mark.django_db(transaction=True)]
EVIDENCE = Path(__file__).resolve().parents[1] / ".tmp/screenshots/issue-421"


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
            (first, 2025, "https://github.com/example/" + "long-project-" * 9 + "capstone"),
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
                "title": "Capstone: " + "Documenting a practical pipeline " * 3,
                "submission_due_date": timezone.now() + timedelta(days=1),
                "peer_review_due_date": timezone.now() + timedelta(days=2),
            },
        )
        learner = User.objects.create_user(username=f"gallery-learner-{index}")
        enrollment = Enrollment.objects.create(
            student=learner,
            course=cohort,
            display_name=(
                "LearnerWithAnExceptionallyLongUnbrokenPublicDisplayName"
                if index == 0
                else f"Gallery Learner {index}"
            ),
        )
        rows.append(
            ProjectSubmission.objects.create(
                project=project,
                student=learner,
                enrollment=enrollment,
                github_link=repository,
                passed=index != 1,
            )
        )
    return first, second, rows


def _set_theme(page: Page, theme: str) -> None:
    is_dark = page.locator("body.dark-mode").count() == 1
    if is_dark != (theme == "dark"):
        page.locator("#dark-mode-toggle").click()
    expect(page.locator("body.dark-mode")).to_have_count(int(theme == "dark"))


@pytest.mark.parametrize("width", [1440, 768, 390, 320])
def test_project_gallery_dependency_table_and_statuses_in_both_themes(
    page: Page, live_server, project_gallery_data, width: int
) -> None:
    family, _, rows = project_gallery_data
    page.set_viewport_size({"width": width, "height": 900 if width >= 768 else 844})
    page.emulate_media(reduced_motion="reduce")
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    gallery_url = f"{live_server.url}{reverse('all_projects')}"

    for theme in ("light", "dark"):
        page.goto(gallery_url)
        _set_theme(page, theme)
        page.evaluate("document.fonts.ready")

        expect(page.get_by_label("Cohort", exact=True)).to_be_disabled()
        expect(page.get_by_label("Assignment", exact=True)).to_be_disabled()
        expect(page.get_by_role("button", name="Apply filters", exact=True)).to_be_visible()
        expect(page.get_by_role("heading", name="Projects", exact=True)).to_be_visible()
        expect(page.locator(".gallery-table th", has_text="Reviews")).to_have_count(1)
        expect(page.locator(".gallery-table th", has_text="Status")).to_have_count(1)
        if width > 768:
            expect(page.get_by_role("columnheader", name="Reviews", exact=True)).to_be_visible()
            expect(page.get_by_role("columnheader", name="Status", exact=True)).to_be_visible()
        else:
            review_cells = page.locator('.gallery-submission-row td[data-label="Reviews"]')
            expect(review_cells).to_have_count(2)
            expect(page.locator('.gallery-submission-row td[data-label="Status"]')).to_have_count(2)
        expect(page.locator(".gallery-submission-row")).to_have_count(2)
        expect(page.locator(".gallery-submission-row").last).to_contain_text(
            "Repository link unavailable"
        )

        page.get_by_label("Course", exact=True).select_option(family.slug)
        expect(page).to_have_url(gallery_url + f"?course={family.slug}&sort=cohort")
        expect(page.get_by_label("Cohort", exact=True)).to_be_enabled()
        expect(page.get_by_label("Assignment", exact=True)).to_be_disabled()

        cohort = rows[0].project.course.identifier
        page.get_by_label("Cohort", exact=True).select_option(cohort)
        expect(page.get_by_label("Assignment", exact=True)).to_be_enabled()
        expect(page.locator(".gallery-submission-row")).to_have_count(2)
        expect(page.locator(".gallery-status.is-passed")).to_have_text("Passed")
        expect(page.locator(".gallery-status:not(.is-passed)")).to_have_text("Not passed")
        page.get_by_label("Assignment", exact=True).select_option("capstone")
        expect(page.get_by_label("Assignment", exact=True)).to_have_value("capstone")

        repository = page.locator(".gallery-cell-repository a").first
        expect(repository).to_have_attribute("href", rows[0].github_link)
        expect(repository).to_have_attribute("rel", "noopener noreferrer")
        assert page.evaluate(
            "document.documentElement.scrollWidth === document.documentElement.clientWidth"
        )
        for target in page.locator(
            ".gallery-filters select, .gallery-filters button, .gallery-filters a, "
            ".gallery-pagination a"
        ).all():
            box = target.bounding_box()
            assert box is not None and box["height"] >= 44
        assert axe_issues(page, f"project-gallery-{width}-{theme}") == []
        page.screenshot(
            path=str(EVIDENCE / f"representative-{width}-{theme}.png"),
            full_page=True,
        )


@pytest.mark.parametrize("width", [1440, 390, 320])
def test_gallery_invalid_filter_focus_and_targets_in_both_themes(
    page: Page, live_server, project_gallery_data, width: int
) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    page.set_viewport_size({"width": width, "height": 900 if width == 1440 else 844})
    gallery_url = f"{live_server.url}{reverse('all_projects')}"
    for theme in ("light", "dark"):
        page.goto(gallery_url + "?course=unknown")
        expect(page.locator(".a11y-error-summary")).to_be_focused()
        _set_theme(page, theme)
        expect(page.get_by_label("Course", exact=True)).to_have_attribute("aria-invalid", "true")
        error_link = page.locator('.a11y-error-summary a[href="#id_course"]')
        box = error_link.bounding_box()
        assert box is not None and box["height"] >= 44
        error_link.click()
        expect(page.get_by_label("Course", exact=True)).to_be_focused()
        assert axe_issues(page, f"project-gallery-invalid-{width}-{theme}") == []
        assert page.evaluate(
            "document.documentElement.scrollWidth === document.documentElement.clientWidth"
        )
        page.screenshot(path=str(EVIDENCE / f"invalid-{width}-{theme}.png"), full_page=True)


@pytest.mark.parametrize("width", [1440, 390, 320])
def test_gallery_compact_pagination_in_both_themes(
    page: Page, live_server, project_gallery_data, width: int
) -> None:
    family, _, rows = project_gallery_data
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    page.set_viewport_size({"width": width, "height": 900 if width == 1440 else 844})
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
    query = f"course={family.slug}&cohort={rows[0].project.course.identifier}&sort=recent"
    for theme in ("light", "dark"):
        page.goto(gallery_url + "?" + query)
        _set_theme(page, theme)
        for number, count in ((1, 25), (2, 25), (3, 1)):
            expect(page.locator(".gallery-submission-row")).to_have_count(count)
            navigation = page.get_by_role("navigation", name="Project submission pages")
            expect(navigation.locator('[aria-current="page"]')).to_contain_text(str(number))
            previous = navigation.locator(".gallery-page-step").filter(has_text="Previous")
            following = navigation.locator(".gallery-page-step").filter(has_text="Next")
            if number == 1:
                expect(previous).to_have_attribute("aria-disabled", "true")
            else:
                expect(previous).not_to_have_attribute("aria-disabled", "true")
            if number == 3:
                expect(following).to_have_attribute("aria-disabled", "true")
            else:
                expect(following).not_to_have_attribute("aria-disabled", "true")
            for target in navigation.locator("a").all():
                box = target.bounding_box()
                assert box is not None and box["height"] >= 44 and box["width"] >= 44
            assert page.evaluate(
                "document.documentElement.scrollWidth === document.documentElement.clientWidth"
            )
            assert axe_issues(page, f"project-gallery-page-{number}-{width}-{theme}") == []
            page.screenshot(
                path=str(EVIDENCE / f"pagination-{number}-{width}-{theme}.png"),
                full_page=True,
            )
            if number < 3:
                following.click()
                expect(page).to_have_url(gallery_url + f"?page={number + 1}&" + query)


def test_gallery_without_any_submissions_has_a_useful_empty_state(page: Page, live_server):
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    page.set_viewport_size({"width": 320, "height": 844})
    page.goto(f"{live_server.url}{reverse('all_projects')}")
    expect(page.get_by_role("heading", name="No project submissions yet")).to_be_visible()
    expect(page.get_by_text("Only public submissions that passed", exact=False)).to_be_visible()
    assert page.evaluate(
        "document.documentElement.scrollWidth === document.documentElement.clientWidth"
    )
    assert axe_issues(page, "project-gallery-no-submissions") == []
    page.screenshot(path=str(EVIDENCE / "no-submissions-320-light.png"), full_page=True)
