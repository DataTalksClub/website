"""Import genuine CMP course content into a local development database.

The local catalogue is seeded from ``scripts/production_like_course_specs.json``, which
carries CMP's *shapes* but invented copy: eighty homework rows reading "Practice
assignment for ...", thirty-two projects described as "Production-like generated", and no
questions at all.  This service replaces that copy with the real thing, read from a
CMP production export.

Scope, deliberately narrow:

* **Content only.**  Courses, homework, questions, projects and review criteria.  No
  account, enrollment, submission, answer, review or registration row is read, so no
  personal data can reach the local database through this path at all.
* **Existing cohorts only.**  A cohort is matched by slug against rows the local database
  already has.  This service never mints a course family, chooses a year, or publishes a
  URL; deriving cohort identity a second way is what split the AI Dev Tools family and
  needed migration ``0052`` to repair.
* **Legacy-format cohorts only.**  A modules-format cohort binds its homework to
  repository-authored modules through ``Module.terminal_homework``, and CMP's homework
  slugs do not always match the repository's.  That pairing belongs in the reviewed
  ``homework_slug_overrides`` manifest hook, so those cohorts are skipped here rather
  than half-imported.

Running it twice is a no-op: every write is keyed on a natural key, and a homework's
questions are replaced as a set.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, NoReturn

from django.db import transaction

from courses.course_family_catalog import COHORT_FAMILY_IDENTITIES
from courses.models import Cohort, Homework, Project, Question, ReviewCriteria

__all__ = [
    "CmpContentImportError",
    "CmpContentImportResult",
    "SKIPPED_COHORTS",
    "import_cmp_course_content",
]


class CmpContentImportError(RuntimeError):
    """A fail-closed refusal that never renders a source value."""


# Cohorts the owner has decided not to publish yet.  They are listed by name with the
# reason attached, rather than left to fall through an unmatched branch: a cohort that
# vanishes because no rule matched is indistinguishable from a bug, while a cohort on
# this list is a decision someone can revisit.  Four of the six need only an entry in
# ``COHORT_FAMILY_IDENTITIES`` to return.
SKIPPED_COHORTS: Mapping[str, str] = {
    "ai-bootcamp-2025": "owner deferred; needs a reviewed family, title and publication state",
    "ai-hero-2025": "owner deferred; needs a reviewed family, title and publication state",
    "ai-hero-2026": "owner deferred; needs a reviewed family, title and publication state",
    "sma-zoomcamp-2026": "owner deferred; needs a reviewed family identity entry",
    "ai-buildcamp-2": (
        "owner deferred; '2' is an edition number, not a year, and the family+year model "
        "cannot express it. Needs design, not a mapping entry"
    ),
    "ai-buildcamp-3": (
        "owner deferred; '3' is an edition number, not a year, and the family+year model "
        "cannot express it. Needs design, not a mapping entry"
    ),
}

# Upstream fixture rows that must never reach a catalogue.
FIXTURE_COHORTS = frozenset({"fake-course", "fake-course-2"})

_COHORT_FIELDS = (
    "title",
    "description",
    "start_date",
    "end_date",
    "registration_url",
    "github_repo_url",
    "social_media_hashtag",
    "first_homework_scored",
    "finished",
    "faq_document_url",
    "min_projects_to_pass",
    "homework_problems_comments_field",
    "project_passing_score",
    "visible",
)
_HOMEWORK_FIELDS = (
    "title",
    "description",
    "due_date",
    "learning_in_public_cap",
    "homework_url_field",
    "time_spent_lectures_field",
    "time_spent_homework_field",
    "faq_contribution_field",
    "state",
    "instructions_url",
)
_QUESTION_FIELDS = (
    "text",
    "question_type",
    "answer_type",
    "possible_answers",
    "correct_answer",
    "scores_for_correct_answer",
)
_PROJECT_FIELDS = (
    "title",
    "description",
    "submission_due_date",
    "learning_in_public_cap_project",
    "peer_review_due_date",
    "time_spent_project_field",
    "problems_comments_field",
    "faq_contribution_field",
    "learning_in_public_cap_review",
    "number_of_peers_to_evaluate",
    "time_spent_evaluation_field",
    "state",
    "points_for_peer_review",
    "instructions_url",
)
_CRITERIA_FIELDS = ("description", "options", "review_criteria_type")

_BOOLEAN_FIELDS = frozenset(
    {
        "first_homework_scored",
        "finished",
        "homework_problems_comments_field",
        "visible",
        "homework_url_field",
        "time_spent_lectures_field",
        "time_spent_homework_field",
        "faq_contribution_field",
        "time_spent_project_field",
        "problems_comments_field",
        "time_spent_evaluation_field",
    }
)
_DATE_FIELDS = frozenset({"start_date", "end_date"})
_DATETIME_FIELDS = frozenset({"due_date", "submission_due_date", "peer_review_due_date"})


@dataclass(frozen=True, slots=True)
class CohortReport:
    """What one cohort contributed, in aggregate counts only."""

    cohort_slug: str
    homework_written: int = 0
    homework_removed: int = 0
    questions_written: int = 0
    projects_written: int = 0
    projects_removed: int = 0
    criteria_written: int = 0


@dataclass(frozen=True, slots=True)
class CmpContentImportResult:
    imported: tuple[CohortReport, ...] = ()
    skipped_by_owner: tuple[tuple[str, str], ...] = ()
    skipped_not_in_local_catalogue: tuple[str, ...] = ()
    skipped_modules_format: tuple[str, ...] = ()
    skipped_fixture: tuple[str, ...] = ()
    skipped_dependent_rows: Mapping[str, int] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            "cohorts_imported": len(self.imported),
            "homework_written": sum(row.homework_written for row in self.imported),
            "homework_removed": sum(row.homework_removed for row in self.imported),
            "questions_written": sum(row.questions_written for row in self.imported),
            "projects_written": sum(row.projects_written for row in self.imported),
            "projects_removed": sum(row.projects_removed for row in self.imported),
            "criteria_written": sum(row.criteria_written for row in self.imported),
            "per_cohort": [
                {
                    "cohort": row.cohort_slug,
                    "homework": row.homework_written,
                    "homework_removed": row.homework_removed,
                    "questions": row.questions_written,
                    "projects": row.projects_written,
                    "projects_removed": row.projects_removed,
                    "criteria": row.criteria_written,
                }
                for row in self.imported
            ],
            "skipped": {
                "by_owner": [
                    {"cohort": slug, "reason": reason} for slug, reason in self.skipped_by_owner
                ],
                "not_in_local_catalogue": list(self.skipped_not_in_local_catalogue),
                "modules_format": list(self.skipped_modules_format),
                "fixture": list(self.skipped_fixture),
                "dependent_rows": dict(self.skipped_dependent_rows),
            },
        }


def _refuse(code: str) -> NoReturn:
    raise CmpContentImportError(code)


def _readonly(source_db: Path) -> sqlite3.Connection:
    try:
        resolved = source_db.expanduser().resolve(strict=True)
    except OSError:
        _refuse("source-unreadable")
    connection = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _assert_content_only(connection: sqlite3.Connection) -> None:
    """Refuse a source whose learner tables would be read by this importer.

    The importer never selects from them, so this is belt-and-braces: it makes the
    content-only boundary a property of the code rather than of the SQL that happens to
    be written below.
    """

    reads = {"courses_course", "courses_homework", "courses_question"}
    reads |= {"courses_project", "courses_reviewcriteria"}
    personal = {
        "accounts_customuser",
        "account_emailaddress",
        "courses_enrollment",
        "courses_submission",
        "courses_answer",
        "courses_projectsubmission",
        "courses_peerreview",
        "courses_criteriaresponse",
        "courses_courseregistration",
        "django_session",
        "socialaccount_socialaccount",
    }
    if reads & personal:
        _refuse("content-boundary-violated")


def _rows(connection: sqlite3.Connection, sql: str, parameters: Sequence[Any] = ()) -> list[Any]:
    try:
        return list(connection.execute(sql, parameters))
    except sqlite3.Error:
        _refuse("source-query-failed")


def _boolean(value: Any) -> bool:
    return bool(value)


def _date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    text = str(value)
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        _refuse("source-date-invalid")


def _datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        _refuse("source-datetime-invalid")
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _coerce(name: str, value: Any) -> Any:
    if name in _BOOLEAN_FIELDS:
        return _boolean(value)
    if name in _DATE_FIELDS:
        return _date(value)
    if name in _DATETIME_FIELDS:
        return _datetime(value)
    if value is None and name in {"title", "description", "state"}:
        return ""
    return value


def _values(row: Any, names: Iterable[str]) -> dict[str, Any]:
    return {name: _coerce(name, row[name]) for name in names}


def _apply(instance: Any, values: Mapping[str, Any]) -> bool:
    """Assign only changed fields so a replayed import writes nothing."""

    changed = [name for name, value in values.items() if getattr(instance, name) != value]
    for name in changed:
        setattr(instance, name, values[name])
    if changed:
        instance.save(update_fields=changed)
    return bool(changed)


def import_cmp_course_content(
    source_db: Path,
    *,
    cohort_slugs: Sequence[str] | None = None,
) -> CmpContentImportResult:
    """Copy real CMP content onto the local catalogue's existing legacy cohorts."""

    connection = _readonly(source_db)
    try:
        _assert_content_only(connection)
        source_cohorts = _rows(connection, "SELECT * FROM courses_course ORDER BY slug")
        local = {cohort.slug: cohort for cohort in Cohort.objects.select_related("course")}

        imported: list[CohortReport] = []
        by_owner: list[tuple[str, str]] = []
        missing: list[str] = []
        modules: list[str] = []
        fixture: list[str] = []
        dependent: dict[str, int] = {}

        for row in source_cohorts:
            slug = str(row["slug"])
            if cohort_slugs is not None and slug not in cohort_slugs:
                continue
            if slug in FIXTURE_COHORTS:
                fixture.append(slug)
                dependent[slug] = _dependent_row_total(connection, row["id"])
                continue
            if slug in SKIPPED_COHORTS:
                by_owner.append((slug, SKIPPED_COHORTS[slug]))
                dependent[slug] = _dependent_row_total(connection, row["id"])
                continue
            cohort = local.get(slug)
            if cohort is None:
                missing.append(slug)
                dependent[slug] = _dependent_row_total(connection, row["id"])
                continue
            if cohort.curriculum_format != Cohort.CurriculumFormat.LEGACY:
                modules.append(slug)
                dependent[slug] = _dependent_row_total(connection, row["id"])
                continue
            if slug in COHORT_FAMILY_IDENTITIES:
                family_slug, _year = COHORT_FAMILY_IDENTITIES[slug]
                if cohort.course.slug != family_slug:
                    _refuse("cohort-family-mismatch")
            with transaction.atomic():
                imported.append(_import_cohort(connection, row, cohort))

        return CmpContentImportResult(
            imported=tuple(imported),
            skipped_by_owner=tuple(by_owner),
            skipped_not_in_local_catalogue=tuple(missing),
            skipped_modules_format=tuple(modules),
            skipped_fixture=tuple(fixture),
            skipped_dependent_rows=dependent,
        )
    finally:
        connection.close()


def _dependent_row_total(connection: sqlite3.Connection, course_id: Any) -> int:
    """Count the learner rows a skipped cohort would have dragged in.

    They are counted, never read: an enrollment graph that imports while its cohort does
    not is exactly the kind of orphan that passes a row-count reconciliation while being
    wrong, so the total is reported rather than left implicit.
    """

    total = 0
    for sql in (
        "SELECT COUNT(*) FROM courses_enrollment WHERE course_id = ?",
        """SELECT COUNT(*) FROM courses_submission s
           JOIN courses_homework h ON s.homework_id = h.id WHERE h.course_id = ?""",
        """SELECT COUNT(*) FROM courses_projectsubmission ps
           JOIN courses_project p ON ps.project_id = p.id WHERE p.course_id = ?""",
    ):
        total += int(_rows(connection, sql, (course_id,))[0][0])
    return total


def _import_cohort(connection: sqlite3.Connection, row: Any, cohort: Cohort) -> CohortReport:
    _apply(cohort, _values(row, _COHORT_FIELDS))
    course_id = row["id"]

    homework_rows = _rows(
        connection,
        "SELECT * FROM courses_homework WHERE course_id = ? ORDER BY id",
        (course_id,),
    )
    written = 0
    questions_written = 0
    source_slugs: set[str] = set()
    for source in homework_rows:
        slug = str(source["slug"])
        source_slugs.add(slug)
        homework, created = Homework.objects.get_or_create(
            course=cohort,
            slug=slug,
            defaults=_values(source, _HOMEWORK_FIELDS),
        )
        if not created:
            _apply(homework, _values(source, _HOMEWORK_FIELDS))
        written += 1
        questions_written += _replace_questions(connection, source["id"], homework)

    # CMP owns a legacy cohort's assignments, so a homework it does not have is seed copy.
    # Module-bound rows cannot occur here: this path only runs for legacy cohorts.
    removed = Homework.objects.filter(course=cohort).exclude(slug__in=source_slugs).delete()[0]

    project_rows = _rows(
        connection,
        "SELECT * FROM courses_project WHERE course_id = ? ORDER BY id",
        (course_id,),
    )
    projects_written = 0
    project_slugs: set[str] = set()
    for source in project_rows:
        slug = str(source["slug"])
        project_slugs.add(slug)
        project, created = Project.objects.get_or_create(
            course=cohort,
            slug=slug,
            defaults=_values(source, _PROJECT_FIELDS),
        )
        if not created:
            _apply(project, _values(source, _PROJECT_FIELDS))
        projects_written += 1
    projects_removed = (
        Project.objects.filter(course=cohort).exclude(slug__in=project_slugs).delete()[0]
    )

    criteria_rows = _rows(
        connection,
        "SELECT * FROM courses_reviewcriteria WHERE course_id = ? ORDER BY id",
        (course_id,),
    )
    ReviewCriteria.objects.filter(course=cohort).delete()
    ReviewCriteria.objects.bulk_create(
        [
            ReviewCriteria(course=cohort, **_values(source, _CRITERIA_FIELDS))
            for source in criteria_rows
        ]
    )

    return CohortReport(
        cohort_slug=cohort.slug,
        homework_written=written,
        homework_removed=removed,
        questions_written=questions_written,
        projects_written=projects_written,
        projects_removed=projects_removed,
        criteria_written=len(criteria_rows),
    )


def _replace_questions(connection: sqlite3.Connection, homework_id: Any, homework: Homework) -> int:
    """Replace a homework's questions as a set.

    A CMP question carries no stable business key, so ordinal replacement is the honest
    idempotency rule: importing twice yields the same set rather than a second copy.
    """

    rows = _rows(
        connection,
        "SELECT * FROM courses_question WHERE homework_id = ? ORDER BY id",
        (homework_id,),
    )
    Question.objects.filter(homework=homework).delete()
    Question.objects.bulk_create(
        [Question(homework=homework, **_values(source, _QUESTION_FIELDS)) for source in rows]
    )
    return len(rows)
