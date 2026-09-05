"""Fail-closed local-development seed for the pinned public course catalog.

Why this exists
---------------
``courses.models.Course`` and ``courses.models.Cohort`` are the single owner of public
course facts (specification 01 "Data ownership", specification 04, and
``_docs/architecture/app-boundaries.md``).  Every public course surface reads them: the
homepage catalogue and featured panel, ``/courses``, ``/courses/<family>`` and
``/courses/<family>/<year>``.  No specification puts public course display on a
projection, and none does now: ``content/public_projection/courses.json`` remains a
checked content artefact with no runtime reader (issue #307).

The seed and that artefact are built from the same pinned upstream catalog:
``scripts/production_like_course_specs.json``, taken from
``DataTalksClub/course-management-platform@98a235283904b4ef9ad29e196298540756cf1bcc``.
``scripts/build_public_projection.py`` verifies that file's SHA-256 before projecting it,
and this seed verifies the same digest before writing rows.  Because both come from the
same pin, a freshly seeded database shows the pinned cohorts rather than any newer ones;
verifying cohort-selection behaviour needs a database built from a current import, not
from this seed.

An empty local database therefore renders an empty course catalogue everywhere.  This
service closes that gap: it writes real cohorts, homework titles, and deadlines into the
local database so ``/`` and ``/courses`` show one catalogue.  It is a development tool
and refuses to run anywhere but a local/test SQLite database.

What it does not do
-------------------
It creates no learners, enrollments, submissions, scores, registrations, or campaigns.
Generating production-like participants remains the adopted
``scripts/generate_production_like_leaderboard_data.py`` path.  Operational state that a
developer or that script has already set (homework/project state, registration URLs,
scoring flags) is preserved: this seed owns catalogue identity only.  If a cohort
already has homework or projects (for example from a CMP content import), this seed
does not add placeholder assignments beside them.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from core.bootstrap import RuntimeEnvironment
from courses.course_family_catalog import (
    COURSE_FAMILY_TITLES,
    cohort_family_identity,
)
from courses.models import (
    Cohort,
    Course,
    Homework,
    HomeworkState,
    Project,
    ProjectState,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CATALOG_SOURCE_PATH = REPOSITORY_ROOT / "scripts" / "production_like_course_specs.json"
PUBLIC_PROJECTION_PATH = (
    REPOSITORY_ROOT / "temporary" / "content" / "public_projection" / "courses.json"
)

# The same pin ``scripts/build_public_projection.py`` verifies before projecting the
# catalogue, and the checksum recorded in every projected course's provenance.
CATALOG_SOURCE_SHA256 = "34077cd485265ffcae96e9acdb06351ee5b6b3b6a5b370639525cc111f1019a7"
CATALOG_SOURCE_REPOSITORY = "DataTalksClub/course-management-platform"
CATALOG_SOURCE_REVISION = "98a235283904b4ef9ad29e196298540756cf1bcc"
CATALOG_SOURCE_RELATIVE_PATH = "scripts/production_like_course_specs.json"

ALLOWED_ENVIRONMENTS = frozenset({RuntimeEnvironment.LOCAL, RuntimeEnvironment.TEST})
SQLITE_ENGINE = "django.db.backends.sqlite3"

CATALOG_SPEC_FIELDS = frozenset({"slug", "title", "finished", "homeworks", "projects"})


class LocalCourseSeedError(RuntimeError):
    """A fail-closed refusal: wrong environment, wrong database, or drifted source."""


@dataclass(frozen=True, slots=True)
class SeededCourse:
    """One catalogue row and what the seed did to it."""

    slug: str
    family_slug: str
    year: int
    title: str
    public_path: str
    created: bool
    homeworks_created: int
    projects_created: int


@dataclass(frozen=True, slots=True)
class LocalCourseSeedResult:
    """Everything a caller needs to report the run without reading the database."""

    courses: tuple[SeededCourse, ...]
    source_path: str
    source_repository: str
    source_revision: str
    source_sha256: str

    @property
    def course_count(self) -> int:
        return len(self.courses)

    @property
    def courses_created(self) -> int:
        return sum(course.created for course in self.courses)

    @property
    def homeworks_created(self) -> int:
        return sum(course.homeworks_created for course in self.courses)

    @property
    def projects_created(self) -> int:
        return sum(course.projects_created for course in self.courses)

    def summary(self) -> dict[str, Any]:
        return {
            "courses": self.course_count,
            "courses_created": self.courses_created,
            "homeworks_created": self.homeworks_created,
            "projects_created": self.projects_created,
            "slugs": [course.slug for course in self.courses],
            "families": sorted({course.family_slug for course in self.courses}),
            "source_path": self.source_path,
            "source_repository": self.source_repository,
            "source_revision": self.source_revision,
            "source_sha256": self.source_sha256,
        }


def assert_local_database() -> None:
    """Refuse to touch anything but a local/test SQLite database."""

    environment = getattr(settings, "RUNTIME_ENVIRONMENT", None)
    if environment not in ALLOWED_ENVIRONMENTS:
        raise LocalCourseSeedError("environment-not-local")
    engine = settings.DATABASES["default"].get("ENGINE")
    if engine != SQLITE_ENGINE:
        raise LocalCourseSeedError("database-not-local-sqlite")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        digest.update(path.read_bytes())
    except OSError as error:
        raise LocalCourseSeedError("catalog-source-unavailable") from error
    return digest.hexdigest()


def load_catalog_specs() -> tuple[dict[str, Any], ...]:
    """Read the pinned upstream catalogue after verifying its exact digest."""

    if _sha256_file(CATALOG_SOURCE_PATH) != CATALOG_SOURCE_SHA256:
        raise LocalCourseSeedError("catalog-source-checksum-mismatch")
    try:
        raw = json.loads(CATALOG_SOURCE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LocalCourseSeedError("catalog-source-invalid") from error
    if not isinstance(raw, list) or not raw:
        raise LocalCourseSeedError("catalog-source-invalid")
    for item in raw:
        if not isinstance(item, dict) or set(item) != CATALOG_SPEC_FIELDS:
            raise LocalCourseSeedError("catalog-source-invalid")
    return tuple(raw)


def load_projected_courses() -> tuple[dict[str, Any], ...]:
    """Read the checked course projection this seed is cross-checked against.

    Nothing renders it; it is kept as the second copy of the same pinned catalogue so a
    drifted seed fails here instead of writing rows the artefact never described.
    """

    try:
        raw = json.loads(PUBLIC_PROJECTION_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LocalCourseSeedError("public-projection-unavailable") from error
    if not isinstance(raw, list) or not raw:
        raise LocalCourseSeedError("public-projection-invalid")
    return tuple(raw)


def _catalog_identity(spec: dict[str, Any]) -> tuple[str, str, bool, int, int]:
    return (
        str(spec["slug"]),
        str(spec["title"]),
        bool(spec["finished"]),
        len(spec["homeworks"]),
        len(spec["projects"]),
    )


def _projected_identity(record: dict[str, Any]) -> tuple[str, str, bool, int, int]:
    return (
        str(record["slug"]),
        str(record["title"]),
        bool(record["finished"]),
        int(record["homework_count"]),
        int(record["project_count"]),
    )


def assert_catalog_matches_projection(
    specs: Sequence[dict[str, Any]],
    projected: Sequence[dict[str, Any]],
) -> None:
    """Fail loudly if the seed and the homepage would show different courses."""

    if {identity[0] for identity in map(_catalog_identity, specs)} != {
        str(record["slug"]) for record in projected
    }:
        raise LocalCourseSeedError("catalog-projection-slug-mismatch")
    if sorted(map(_catalog_identity, specs)) != sorted(map(_projected_identity, projected)):
        raise LocalCourseSeedError("catalog-projection-drift")
    for record in projected:
        family_slug, year = cohort_family_identity(str(record["slug"]))
        if str(record["public_path"]) != f"/courses/{family_slug}/{year}":
            raise LocalCourseSeedError("catalog-projection-path-drift")


def _due_dates(spec: dict[str, Any]) -> tuple[datetime, ...]:
    assignments: list[list[Any]] = [*spec["homeworks"], *spec["projects"]]
    due_dates = []
    for assignment in assignments:
        if not isinstance(assignment, list) or len(assignment) < 2:
            raise LocalCourseSeedError("catalog-source-invalid")
        try:
            due_dates.append(datetime.fromisoformat(str(assignment[1])))
        except ValueError as error:
            raise LocalCourseSeedError("catalog-source-invalid") from error
    if not due_dates:
        raise LocalCourseSeedError("catalog-source-invalid")
    return tuple(due_dates)


def course_bounds(spec: dict[str, Any]) -> tuple[date, date]:
    """Return the run's first and last deadline as dates, as upstream derives them."""

    due_dates = _due_dates(spec)
    return min(due_dates).date(), max(due_dates).date()


def course_description(spec: dict[str, Any]) -> str:
    """Derive the catalogue description from the pinned counts, as upstream does."""

    parts = [
        "Free course with practical lessons",
        f"{len(spec['homeworks'])} homework assignments",
    ]
    if spec["projects"]:
        parts.append(f"{len(spec['projects'])} project attempts")
    return f"{', '.join(parts)} for {spec['title']}."


def assignment_slug(prefix: str, title: str, index: int) -> str:
    """Build the stable assignment slug the adopted catalogue seeder also builds."""

    return f"{prefix}-{index:02d}-{slugify(title)[:45]}"


def homework_description(title: str) -> str:
    return (
        f"Practice assignment for {title}. "
        "Use it to check your understanding before moving to the next module."
    )


def _homework_state(due_date: datetime, now: datetime) -> str:
    return HomeworkState.OPEN.value if due_date > now else HomeworkState.CLOSED.value


def _project_state(due_date: datetime, now: datetime) -> str:
    if due_date > now:
        return ProjectState.COLLECTING_SUBMISSIONS.value
    return ProjectState.CLOSED.value


def _seed_course(spec: dict[str, Any], now: datetime) -> SeededCourse:
    start_date, end_date = course_bounds(spec)
    slug = str(spec["slug"])
    family_slug, year = cohort_family_identity(slug)
    family, _ = Course.objects.update_or_create(
        slug=family_slug,
        defaults={
            "title": COURSE_FAMILY_TITLES[family_slug],
            "description": COURSE_FAMILY_TITLES[family_slug],
            "visible": True,
        },
    )
    identity = {
        "course": family,
        "year": year,
        "title": str(spec["title"]),
        "start_date": start_date,
        "end_date": end_date,
        "finished": bool(spec["finished"]),
        "visible": True,
    }
    course, created = Cohort.objects.update_or_create(
        slug=slug,
        defaults=identity,
        create_defaults={**identity, "description": course_description(spec)},
    )
    homeworks_created = _seed_homeworks(course, spec["homeworks"], now)
    projects_created = _seed_projects(course, spec["projects"], now)
    return SeededCourse(
        slug=slug,
        family_slug=family_slug,
        year=year,
        title=course.title,
        public_path=course.canonical_url_path,
        created=created,
        homeworks_created=homeworks_created,
        projects_created=projects_created,
    )


def _seed_homeworks(course: Cohort, homeworks: Iterable[Sequence[Any]], now: datetime) -> int:
    if Homework.objects.filter(course=course).exists():
        return 0
    created_count = 0
    for index, assignment in enumerate(homeworks, start=1):
        title = str(assignment[0])
        due_date = datetime.fromisoformat(str(assignment[1]))
        identity = {
            "title": title,
            "description": homework_description(title),
            "due_date": due_date,
        }
        _, created = Homework.objects.update_or_create(
            course=course,
            slug=assignment_slug("homework", title, index),
            defaults=identity,
            create_defaults={**identity, "state": _homework_state(due_date, now)},
        )
        created_count += int(created)
    return created_count


def _seed_projects(course: Cohort, projects: Iterable[Sequence[Any]], now: datetime) -> int:
    if Project.objects.filter(course=course).exists():
        return 0
    created_count = 0
    for index, assignment in enumerate(projects, start=1):
        title = str(assignment[0])
        due_date = datetime.fromisoformat(str(assignment[1]))
        identity = {
            "title": title,
            "description": f"Production-like generated project: {title}",
            "submission_due_date": due_date,
            "peer_review_due_date": due_date,
        }
        _, created = Project.objects.update_or_create(
            course=course,
            slug=assignment_slug("project", title, index),
            defaults=identity,
            create_defaults={**identity, "state": _project_state(due_date, now)},
        )
        created_count += int(created)
    return created_count


def seed_local_courses() -> LocalCourseSeedResult:
    """Make the local database agree with the checked public course projection."""

    assert_local_database()
    specs = load_catalog_specs()
    projected = load_projected_courses()
    assert_catalog_matches_projection(specs, projected)

    now = timezone.now()
    ordered = sorted(specs, key=lambda spec: str(spec["slug"]))
    with transaction.atomic():
        seeded = tuple(_seed_course(spec, now) for spec in ordered)
    return LocalCourseSeedResult(
        courses=seeded,
        source_path=CATALOG_SOURCE_RELATIVE_PATH,
        source_repository=CATALOG_SOURCE_REPOSITORY,
        source_revision=CATALOG_SOURCE_REVISION,
        source_sha256=CATALOG_SOURCE_SHA256,
    )
