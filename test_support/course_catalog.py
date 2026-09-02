"""Deterministic course-family fixtures for the public course surfaces.

Public course pages read ``courses.Course`` / ``courses.Cohort`` directly, so their tests
need real rows.  They must not be built through ``courses.services.local_course_seed``:
that seed writes the cohorts of one pinned upstream revision, and a test standing on it
would silently assert the pin rather than the behaviour under test.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from courses.models.cohort import Cohort, Course
from courses.models.homework import Homework
from courses.models.project import Project


def make_family(slug: str, title: str, *, visible: bool = True) -> Course:
    family, _created = Course.objects.get_or_create(
        slug=slug,
        defaults={"title": title, "visible": visible},
    )
    if family.title != title or family.visible != visible:
        family.title = title
        family.visible = visible
        family.save(update_fields=["title", "visible"])
    return family


def make_cohort(
    family: Course,
    year: int,
    *,
    slug: str | None = None,
    title: str | None = None,
    visible: bool = True,
    start_date: date | None = None,
    homework_count: int = 0,
    project_count: int = 0,
) -> Cohort:
    cohort = Cohort.objects.create(
        course=family,
        slug=slug or f"{family.slug}-{year}",
        identifier=str(year),
        year=year,
        title=title or f"{family.title} {year}",
        description="",
        visible=visible,
        start_date=start_date or date(year, 8, 31),
    )
    due = datetime(year, 9, 30, 12, 0, tzinfo=UTC)
    for index in range(homework_count):
        Homework.objects.create(
            course=cohort,
            slug=f"{cohort.slug}-hw{index + 1}",
            title=f"Homework {index + 1}",
            due_date=due + timedelta(days=index),
        )
    for index in range(project_count):
        Project.objects.create(
            course=cohort,
            slug=f"{cohort.slug}-project{index + 1}",
            title=f"Project {index + 1}",
            submission_due_date=due + timedelta(days=30 + index),
            peer_review_due_date=due + timedelta(days=37 + index),
        )
    return cohort


def build_reviewed_catalog() -> dict[str, Cohort]:
    """Build the current dataset's shape: six courses, AI Dev Tools split in two.

    Returns the newest cohort of every family, keyed by family slug.
    """

    newest: dict[str, Cohort] = {}
    ai_dev_tools = make_family("ai-dev-tools", "AI Dev Tools Zoomcamp")
    newest["ai-dev-tools"] = make_cohort(
        ai_dev_tools,
        2025,
        start_date=date(2025, 11, 27),
        homework_count=3,
        project_count=2,
    )
    ai_dev_tools_zoomcamp = make_family("ai-dev-tools-zoomcamp", "AI Dev Tools Zoomcamp")
    newest["ai-dev-tools-zoomcamp"] = make_cohort(
        ai_dev_tools_zoomcamp,
        2026,
        start_date=date(2026, 8, 31),
        homework_count=4,
        project_count=0,
    )
    for slug, title, newest_year, start in (
        ("de-zoomcamp", "Data Engineering Zoomcamp", 2026, date(2026, 1, 26)),
        ("llm-zoomcamp", "LLM Zoomcamp", 2026, date(2026, 6, 8)),
        ("ml-zoomcamp", "Machine Learning Zoomcamp", 2026, date(2026, 9, 14)),
        ("mlops-zoomcamp", "MLOps Zoomcamp", 2025, date(2025, 5, 19)),
        ("sma-zoomcamp", "Stock Markets Analytics Zoomcamp", 2025, date(2025, 6, 4)),
    ):
        family = make_family(slug, title)
        make_cohort(family, newest_year - 1, start_date=start.replace(year=start.year - 1))
        newest[slug] = make_cohort(
            family,
            newest_year,
            start_date=start,
            homework_count=5,
            project_count=2,
        )
    return newest
