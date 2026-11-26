"""Import genuine CMP course content into a local development database.

The local catalogue is seeded from ``scripts/production_like_course_specs.json``, which
carries CMP's *shapes* but invented copy: eighty homework rows reading "Practice
assignment for ...", thirty-two projects described as "Production-like generated", and no
questions at all.  This service replaces that copy with the real thing, read from a
CMP production export.

Scope, deliberately narrow:

* **Content only.**  Courses, homework, questions, projects, review criteria and
  registration *campaigns*.  No account, enrollment, submission, answer, review or
  learner registration row is read, so no personal data can reach the local database
  through this path at all.  A ``RegistrationCampaign`` is the marketing definition of an
  open registration -- its slug, copy, dates and the cohort it promotes.  The learner
  rows that hang off it live in ``courses_courseregistration`` and are never touched.
* **Derived cohort identity, mechanically.**  A cohort is matched by slug against the
  rows the local database already has.  When the local catalogue is missing a cohort,
  its family and year are parsed from the edition slug itself (``"de-zoomcamp-2022"``
  -> family ``"de-zoomcamp"``, year ``2022``); the family's title, when the family row
  does not exist yet either, is read from CMP's own cohort title with the year
  stripped.  Nothing is invented.  The one exception is a small, explicit
  ``family_slug_overrides`` correction the caller supplies for an edition slug CMP
  exports under an irregular family spelling (``"ai-dev-tools-2025"``, which omits the
  ``-zoomcamp`` its real family carries) -- see ``scripts/prod/import_cmp_content.py``.
  A slug that doesn't parse as ``<family>-<year>`` at all stays missing and is reported.
* **CMP owns homework content; the repository owns a repository row's slug.**  On a
  legacy cohort CMP is the whole source, so a homework's slug is whatever CMP says it
  is.  On a modules or shared cohort a repository-authored row (one carrying
  ``source_content_id``) keeps the slug its ``homework.yaml`` declares: that slug is
  bound into the encrypted answer envelopes and published URLs, and the course
  repository import refuses to change it once learners have submitted.  CMP's fields
  are copied onto the row, CMP's slug is not.  A row nobody owns still takes CMP's slug.

  The pairing is read from data both sides already publish -- the slug when they agree,
  otherwise an exact title match, otherwise the reviewed ``homework_slug_overrides``
  entry -- and anything that does not pair is **left unbound and reported**.  Guessing
  by ordinal position would misattach CMP's ``dlt`` workshop to the sixth LLM module,
  which renders as a page that looks fine and is wrong.

  Each pairing is recorded as a ``CmpHistoryClaim`` under
  :data:`HOMEWORK_PAIRING_TABLE` (CMP homework id -> local homework pk), because once the
  slugs differ nothing else ties the two together: a replay finds its row through the
  claim even after a repository pull rewrote the title, and the learner-history importer
  resolves CMP submissions through the same claim (:func:`homework_pairings`).

  A paired row is never replaced.  The row stays the repository's, keeping
  ``source_content_id``, the imported instructions Markdown, its source path, its units
  and its module binding.  Replacing it left a homework no import owned, which is what
  the course-repository path refuses on its next pull with ``homework_slug_collision``.

  ``homework_slug_overrides`` (``{repository_slug: cmp_slug}`` per course) also repairs
  a repository row an earlier version of this import renamed to CMP's slug: such a row
  is renamed back to the repository slug its override names.

Running it twice is a no-op: every write is keyed on the same natural key the learner
history importer uses. Questions reconcile by text and source order, and review criteria
by description, so their target primary keys and imported learner references survive a
content replay.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, NoReturn

from django.db import transaction
from django.db.models.deletion import ProtectedError

from accounts.services.cmp_learner_import import _readonly
from courses.models import (
    Answer,
    CmpHistoryClaim,
    Cohort,
    Homework,
    Module,
    Project,
    ProjectSubmission,
    Question,
    ReviewCriteria,
    Submission,
)
from courses.models.cohort import Course, RegistrationCampaign
from courses.services.course_family_identity import (
    UnparseableEditionSlug,
    family_and_year_from_edition_slug,
    family_title_from_edition_title,
)
from courses.services.launch_import_timing import import_explicit_start
from courses.services.launch_validation import LaunchError

__all__ = [
    "HOMEWORK_PAIRING_TABLE",
    "CampaignReport",
    "CmpContentImportError",
    "CmpContentImportResult",
    "SKIPPED_COHORTS",
    "PROJECT_PAIRING_TABLE",
    "homework_pairings",
    "import_cmp_course_content",
    "project_pairings",
]

# The ``CmpHistoryClaim.table`` key under which this import records which local homework
# each CMP homework was paired with.  It is not one of the learner-history tables, so the
# two importers' claims never collide.
HOMEWORK_PAIRING_TABLE = "cmp_content:courses_homework"
# The same record for projects paired through ``project_slug_overrides``.
PROJECT_PAIRING_TABLE = "cmp_content:courses_project"


def _pairings(table: str) -> dict[int, int]:
    return {
        int(source_id): int(target_id)
        for source_id, target_id in CmpHistoryClaim.objects.filter(table=table).values_list(
            "source_id", "target_id"
        )
    }


def homework_pairings() -> dict[int, int]:
    """CMP homework id -> local homework pk, as this import last paired them."""

    return _pairings(HOMEWORK_PAIRING_TABLE)


def project_pairings() -> dict[int, int]:
    """CMP project id -> local project pk, as this import last paired them."""

    return _pairings(PROJECT_PAIRING_TABLE)


def _record_pairing(source_id: int, target_id: int, table: str = HOMEWORK_PAIRING_TABLE) -> None:
    CmpHistoryClaim.objects.update_or_create(
        table=table, source_id=source_id, defaults={"target_id": target_id}
    )


class CmpContentImportError(RuntimeError):
    """A fail-closed refusal that never renders a source value."""


# Cohorts the owner has decided not to publish yet.  They are listed by name with the
# reason attached, rather than left to fall through an unmatched branch: a cohort that
# vanishes because no rule matched is indistinguishable from a bug, while a cohort on
# this list is a decision someone can revisit.
SKIPPED_COHORTS: Mapping[str, str] = {
    "ai-bootcamp-2025": "owner deferred; needs a reviewed family, title and publication state",
    "ai-hero-2025": "owner deferred; needs a reviewed family, title and publication state",
    "ai-hero-2026": "owner deferred; needs a reviewed family, title and publication state",
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
# What a repository-authored project's cohort.yaml entry owns (see
# ``courses.services.curriculum_import._import_cohort_projects``).
_REPOSITORY_PROJECT_FIELDS = frozenset(
    {
        "title",
        "description",
        "submission_due_date",
        "peer_review_due_date",
        "number_of_peers_to_evaluate",
    }
)
_CRITERIA_FIELDS = ("description", "options", "review_criteria_type")
# A campaign row is editorial: what is open, under which slug, with which copy.  The
# learner rows that reference it are personal data and are never read.
_CAMPAIGN_FIELDS = (
    "title",
    "edition_label",
    "is_active",
    "marketing_markdown",
    "meta_description",
    "hero_image_url",
    "video_url",
)

_BOOLEAN_FIELDS = frozenset(
    {
        "first_homework_scored",
        "finished",
        "homework_problems_comments_field",
        "visible",
        "is_active",
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
# Text columns CMP may leave NULL that this schema declares ``blank=True`` and NOT NULL.
_NULLABLE_TEXT_FIELDS = frozenset(
    {
        "title",
        "description",
        "state",
        "edition_label",
        "marketing_markdown",
        "meta_description",
        "hero_image_url",
        "video_url",
    }
)
_DATETIME_FIELDS = frozenset({"due_date", "submission_due_date", "peer_review_due_date"})


@dataclass(frozen=True, slots=True)
class CohortReport:
    """What one cohort contributed, in aggregate counts only."""

    cohort_slug: str
    homework_written: int = 0
    homework_removed: int = 0
    questions_written: int = 0
    questions_retained: int = 0
    repository_questions_kept: int = 0
    projects_written: int = 0
    projects_removed: int = 0
    criteria_written: int = 0
    criteria_retained: int = 0
    rebound_modules: tuple[tuple[str, str, str], ...] = ()
    unpaired_cmp_homework: tuple[str, ...] = ()
    unpaired_repository_homework: tuple[str, ...] = ()
    unpaired_repository_projects: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CampaignReport:
    """What one registration campaign contributed. Definition only, never a learner."""

    campaign_slug: str
    created: bool
    promoted_cohort: str = ""


@dataclass(frozen=True, slots=True)
class CmpContentImportResult:
    imported: tuple[CohortReport, ...] = ()
    created_cohorts: tuple[str, ...] = ()
    created_families: tuple[str, ...] = ()
    campaigns: tuple[CampaignReport, ...] = ()
    campaigns_without_a_local_cohort: tuple[str, ...] = ()
    skipped_by_owner: tuple[tuple[str, str], ...] = ()
    skipped_not_in_local_catalogue: tuple[str, ...] = ()
    skipped_fixture: tuple[str, ...] = ()
    skipped_dependent_rows: Mapping[str, int] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            "cohorts_imported": len(self.imported),
            "cohorts_created": list(self.created_cohorts),
            "families_created": list(self.created_families),
            "campaigns_written": len(self.campaigns),
            "campaigns_created": sum(1 for row in self.campaigns if row.created),
            "campaigns": [
                {
                    "slug": row.campaign_slug,
                    "created": row.created,
                    "promotes": row.promoted_cohort,
                }
                for row in self.campaigns
            ],
            "campaigns_without_a_local_cohort": list(self.campaigns_without_a_local_cohort),
            "homework_written": sum(row.homework_written for row in self.imported),
            "homework_removed": sum(row.homework_removed for row in self.imported),
            "questions_written": sum(row.questions_written for row in self.imported),
            "questions_retained": sum(row.questions_retained for row in self.imported),
            "repository_questions_kept": sum(
                row.repository_questions_kept for row in self.imported
            ),
            "projects_written": sum(row.projects_written for row in self.imported),
            "projects_removed": sum(row.projects_removed for row in self.imported),
            "criteria_written": sum(row.criteria_written for row in self.imported),
            "criteria_retained": sum(row.criteria_retained for row in self.imported),
            "modules_rebound": sum(len(row.rebound_modules) for row in self.imported),
            "unpaired_cmp_homework": sorted(
                slug for row in self.imported for slug in row.unpaired_cmp_homework
            ),
            "unpaired_repository_homework": sorted(
                slug for row in self.imported for slug in row.unpaired_repository_homework
            ),
            "unpaired_repository_projects": sorted(
                slug for row in self.imported for slug in row.unpaired_repository_projects
            ),
            "rebindings": [
                {"cohort": row.cohort_slug, "module": module, "was": old, "now": new}
                for row in self.imported
                for module, old, new in row.rebound_modules
            ],
            "per_cohort": [
                {
                    "cohort": row.cohort_slug,
                    "homework": row.homework_written,
                    "homework_removed": row.homework_removed,
                    "questions": row.questions_written,
                    "questions_retained": row.questions_retained,
                    "repository_questions_kept": row.repository_questions_kept,
                    "projects": row.projects_written,
                    "projects_removed": row.projects_removed,
                    "criteria": row.criteria_written,
                    "criteria_retained": row.criteria_retained,
                }
                for row in self.imported
            ],
            "skipped": {
                "by_owner": [
                    {"cohort": slug, "reason": reason} for slug, reason in self.skipped_by_owner
                ],
                "not_in_local_catalogue": list(self.skipped_not_in_local_catalogue),
                "fixture": list(self.skipped_fixture),
                "dependent_rows": dict(self.skipped_dependent_rows),
            },
        }


def _refuse(code: str) -> NoReturn:
    raise CmpContentImportError(code)


def _assert_content_only(connection: sqlite3.Connection) -> None:
    """Refuse a source whose learner tables would be read by this importer.

    The importer never selects from them, so this is belt-and-braces: it makes the
    content-only boundary a property of the code rather than of the SQL that happens to
    be written below.
    """

    reads = {"courses_course", "courses_homework", "courses_question"}
    reads |= {"courses_project", "courses_reviewcriteria", "courses_registrationcampaign"}
    personal = {
        "accounts_customuser",
        "accounts_token",
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
        "socialaccount_socialapp",
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
    """Read one CMP timestamp, interpreting an offset-less value as UTC.

    CMP runs Django with ``TIME_ZONE = "UTC"`` and ``USE_TZ = True``
    (``course_management/settings.py``), and its SQLite backend stores every aware
    datetime as a naive UTC string (``"2026-01-12 00:00:00"``).  So a naive value in
    the export *is* UTC, by CMP's own configuration -- reading it as such here, at
    the import boundary, is the documented interpretation, not a guess.  Nothing
    past this function ever sees a naive value.
    """

    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            _refuse("source-datetime-invalid")
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _is_date_only(value: Any) -> bool:
    """A start carrying a calendar date and no time of day."""

    if isinstance(value, datetime):
        return False
    if isinstance(value, date):
        return True
    if isinstance(value, str):
        text = value.strip()
        return len(text) == 10 and text[4] == "-" and text[7] == "-"
    return False


def _import_explicit_start(row: Any, source: Any, kind: str) -> None:
    """Hand CMP's optional ``starts_at`` to the shared timing seam, already aware.

    ``courses.services.launch_import_timing`` deliberately accepts only an
    offset-carrying timestamp, so the CMP-specific interpretation (naive means
    UTC, see :func:`_datetime`) happens here rather than loosening that seam for
    every caller.  A start the launch rules reject (after the deadline, after the
    cohort end) is a bounded refusal like every other bad source value, not an
    uncaught ``LaunchError``.
    """

    explicit: dict[str, Any] = {}
    if "starts_at" in source.keys():
        raw = source["starts_at"]
        if _is_date_only(raw):
            # The provenance this records says date-only values are not
            # inferred: a bare date names no instant, so it is refused rather
            # than silently read as midnight UTC.
            _refuse(f"source-{kind}-start-date-only")
        explicit["starts_at"] = _datetime(raw)
    try:
        import_explicit_start(row, explicit, kind)
    except LaunchError:
        _refuse(f"source-{kind}-start-invalid")


def _coerce(name: str, value: Any) -> Any:
    if name in _BOOLEAN_FIELDS:
        return _boolean(value)
    if name in _DATE_FIELDS:
        return _date(value)
    if name in _DATETIME_FIELDS:
        return _datetime(value)
    if value is None and name in _NULLABLE_TEXT_FIELDS:
        return ""
    if name == "possible_answers" and value == "":
        # CMP stores "no options" as an empty string; the course repository import
        # stores it as NULL and compares definitions exactly, so normalize at the
        # boundary instead of flipping the column on every alternate import.
        return None
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
    family_slug_overrides: Mapping[str, str] = MappingProxyType({}),
    cohort_slug_overrides: Mapping[str, str] = MappingProxyType({}),
    homework_slug_overrides: Mapping[str, Mapping[str, str]] = MappingProxyType({}),
    project_slug_overrides: Mapping[str, Mapping[str, str]] = MappingProxyType({}),
) -> CmpContentImportResult:
    """Copy real CMP content onto the local catalogue's existing legacy cohorts.

    ``family_slug_overrides`` corrects a CMP edition slug whose family portion does not
    match its real repository family (for example ``ai-dev-tools-2025``, which CMP
    exports without the ``-zoomcamp`` suffix the real ``ai-dev-tools-zoomcamp`` family
    carries).  Every other edition slug's family and year are derived mechanically from
    the slug itself.  The caller -- ``scripts/prod/import_cmp_content.py`` -- owns this
    small, reviewed correction; this function does not guess.

    ``homework_slug_overrides`` corrects a course whose repository declares a homework
    slug CMP does not use for the same assignment (see ``_import_cohort``).  Keyed by
    course slug, valued by a ``{repository_slug: cmp_slug}`` mapping.
    """

    connection = _readonly(source_db, error_cls=CmpContentImportError)
    try:
        _assert_content_only(connection)
        source_cohorts = _rows(connection, "SELECT * FROM courses_course ORDER BY slug")
        local = {cohort.slug: cohort for cohort in Cohort.objects.select_related("course")}

        imported: list[CohortReport] = []
        created: list[str] = []
        created_families: list[str] = []
        by_owner: list[tuple[str, str]] = []
        missing: list[str] = []
        fixture: list[str] = []
        dependent: dict[str, int] = {}

        for row in source_cohorts:
            raw_slug = str(row["slug"])
            slug = cohort_slug_overrides.get(raw_slug, raw_slug)
            if (
                cohort_slugs is not None
                and raw_slug not in cohort_slugs
                and slug not in cohort_slugs
            ):
                continue
            if raw_slug in FIXTURE_COHORTS or slug in FIXTURE_COHORTS:
                fixture.append(raw_slug)
                dependent[raw_slug] = _dependent_row_total(connection, row["id"])
                continue
            if raw_slug in SKIPPED_COHORTS or slug in SKIPPED_COHORTS:
                reason = SKIPPED_COHORTS.get(raw_slug) or SKIPPED_COHORTS[slug]
                by_owner.append((raw_slug, reason))
                dependent[raw_slug] = _dependent_row_total(connection, row["id"])
                continue
            identity = _family_and_year(slug, family_slug_overrides)
            cohort = local.get(slug)
            if cohort is None and identity is not None:
                # A cohort adopted (or repository-synced) under a corrected family
                # spelling carries the *corrected* slug locally -- e.g. the real
                # ``ai-dev-tools-zoomcamp-2025``, not CMP's own ``ai-dev-tools-2025``.
                # Check that corrected identity before treating the edition as new,
                # so an override doesn't mint a second cohort under the raw CMP slug.
                cohort = local.get(f"{identity[0]}-{identity[1]}")
            if cohort is None:
                cohort = _adopt_reviewed_cohort(
                    slug, row, created_families=created_families, overrides=family_slug_overrides
                )
                if cohort is None:
                    missing.append(raw_slug)
                    dependent[raw_slug] = _dependent_row_total(connection, row["id"])
                    continue
                local[slug] = cohort
                created.append(slug)
            local[raw_slug] = cohort
            if identity is not None:
                family_slug, _year = identity
                if cohort.course.slug != family_slug:
                    _refuse("cohort-family-mismatch")
            with transaction.atomic():
                imported.append(
                    _import_cohort(
                        connection,
                        row,
                        cohort,
                        homework_slug_overrides,
                        project_slug_overrides,
                    )
                )

        campaigns, unlinked = _import_registration_campaigns(
            connection,
            source_cohorts,
            local,
            cohort_slugs,
            cohort_slug_overrides=cohort_slug_overrides,
        )

        return CmpContentImportResult(
            imported=tuple(imported),
            created_cohorts=tuple(created),
            created_families=tuple(dict.fromkeys(created_families)),
            campaigns=campaigns,
            campaigns_without_a_local_cohort=unlinked,
            skipped_by_owner=tuple(by_owner),
            skipped_not_in_local_catalogue=tuple(missing),
            skipped_fixture=tuple(fixture),
            skipped_dependent_rows=dependent,
        )
    finally:
        connection.close()


def _family_and_year(slug: str, overrides: Mapping[str, str]) -> tuple[str, int] | None:
    """Derive a CMP edition slug's family and year, applying known corrections.

    Almost every CMP edition slug's family is exactly its own de-suffixed form
    (``"de-zoomcamp-2022"`` -> ``"de-zoomcamp"``), so that is derived mechanically.  The
    handful CMP exports under an irregular family spelling are corrected by
    ``overrides`` (a family-slug replacement keyed by the *mechanically derived* family),
    supplied by the caller -- see ``scripts/prod/import_cmp_content.py``.  Returns
    ``None`` for a slug that does not even parse as ``<family>-<year>`` -- e.g. an
    edition-number slug like ``"ai-buildcamp-2"`` -- since there is nothing to correct.
    """

    try:
        family_slug, year = family_and_year_from_edition_slug(slug)
    except UnparseableEditionSlug:
        return None
    return overrides.get(family_slug, family_slug), year


def _adopt_reviewed_cohort(
    slug: str,
    row: Any,
    *,
    created_families: list[str] | None = None,
    overrides: Mapping[str, str] = MappingProxyType({}),
) -> Cohort | None:
    """Create a local cohort CMP publishes and the local catalogue is missing.

    Only a slug this module can parse as ``<family>-<year>`` (after ``overrides``
    corrects a known irregular family spelling) can be adopted.  A slug that doesn't
    parse -- an edition-number slug like ``"ai-buildcamp-2"``, or anything genuinely
    unrecognisable -- stays missing and is reported rather than guessed at.

    The family row is created when it is absent, with a title derived mechanically from
    this row's own ``title`` (the same trailing-year strip ``Cohort.save()`` uses for a
    cohort's own family).  Requiring a pre-existing family made a production ingest
    impossible: on an empty database no family exists, so every cohort was reported
    missing and the import wrote nothing at all unless a placeholder seeder had run
    first.
    """

    identity = _family_and_year(slug, overrides)
    if identity is None:
        return None
    family_slug, year = identity
    family = Course.objects.filter(slug=family_slug).first()
    if family is None:
        title = (
            family_title_from_edition_title(str(row["title"] or ""))
            or family_slug.replace("-", " ").title()
        )
        family = Course.objects.create(slug=family_slug, title=title)
        if created_families is not None:
            created_families.append(family_slug)
    return Cohort.objects.create(
        course=family,
        slug=slug,
        identifier=str(year),
        year=year,
        curriculum_format=Cohort.CurriculumFormat.LEGACY,
        **_values(row, _COHORT_FIELDS),
    )


def _import_registration_campaigns(
    connection: sqlite3.Connection,
    source_cohorts: Sequence[Any],
    local: Mapping[str, Cohort],
    cohort_slugs: Sequence[str] | None = None,
    *,
    cohort_slug_overrides: Mapping[str, str] = MappingProxyType({}),
) -> tuple[tuple[CampaignReport, ...], tuple[str, ...]]:
    """Copy CMP's registration campaign *definitions*, keyed by slug.

    A campaign says what is open, under which public slug, with which copy, and which
    cohort it promotes.  The learners who registered through it are a different table and
    are never read.  ``current_course`` is resolved through the local catalogue, so a
    campaign whose cohort this database does not hold arrives unlinked and is reported
    rather than silently pointing at nothing.
    """

    raw_slug_by_source_id = {row["id"]: str(row["slug"]) for row in source_cohorts}
    slug_by_source_id = {
        row["id"]: cohort_slug_overrides.get(str(row["slug"]), str(row["slug"]))
        for row in source_cohorts
    }
    rows = _rows(connection, "SELECT * FROM courses_registrationcampaign ORDER BY slug")
    existing = {campaign.slug: campaign for campaign in RegistrationCampaign.objects.all()}

    reports: list[CampaignReport] = []
    unlinked: list[str] = []
    for source in rows:
        slug = str(source["slug"])
        raw_promoted_slug = raw_slug_by_source_id.get(source["current_course_id"], "")
        promoted_slug = slug_by_source_id.get(source["current_course_id"], "")
        if (
            cohort_slugs is not None
            and raw_promoted_slug not in cohort_slugs
            and promoted_slug not in cohort_slugs
        ):
            continue
        cohort = local.get(promoted_slug) if promoted_slug else None
        if promoted_slug and cohort is None:
            unlinked.append(slug)
        values = {**_values(source, _CAMPAIGN_FIELDS), "current_course": cohort}
        campaign = existing.get(slug)
        with transaction.atomic():
            if campaign is None:
                campaign = RegistrationCampaign.objects.create(slug=slug, **values)
                created = True
            else:
                _apply(campaign, values)
                created = False
        reports.append(
            CampaignReport(
                campaign_slug=slug,
                created=created,
                promoted_cohort=cohort.slug if cohort is not None else "",
            )
        )
    return tuple(reports), tuple(unlinked)


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


def _repository_pair(
    candidates: dict[str, Homework],
    key: Any,
    slug_match: Homework | None,
) -> Homework | None:
    """Return the repository row this CMP row is the same assignment as, if any.

    ``candidates`` is keyed either by exact title (the general case) or, for a course
    with a reviewed ``homework_slug_overrides`` entry, by the CMP slug its irregular
    repository slug is known to mean.  Either way the pairing is tried whether or not a
    row already carries CMP's slug.  Trying it only on the create branch made the import
    order-dependent: an assignment CMP and a repository both describe reconciled on a
    first run and stayed permanently duplicated on every later one, because the
    CMP-slugged row from the previous run matched first and the repository row was never
    looked at again.  The local dataset copies CMP before it pulls a repository, so
    *every* pairing there hits that branch.
    """

    paired = candidates.get(str(key))
    if paired is None:
        return None
    if slug_match is None:
        return candidates.pop(str(key))
    if paired.pk == slug_match.pk:
        return None
    # Two rows for one assignment: the repository's, and one already carrying CMP's slug.
    # Folding them discards a row, so it is done only when the surviving row is the one a
    # repository import owns, and never when the discarded row has submissions attached.
    if paired.source_content_id is None:
        return None
    if Submission.objects.filter(homework=slug_match).exists():
        return None
    return candidates.pop(str(key))


def _adopt_repository_row(
    paired: Homework,
    slug_match: Homework | None,
    existing: dict[str, Homework],
) -> Homework:
    """Make the repository's row the one CMP's assignment lands on, in place.

    A second row already carrying CMP's slug (left by an earlier import, before the
    repository row existed) is folded away; ``_repository_pair`` only offers a pair when
    that row has no submissions.  The surviving row stays the repository's: replacing it
    would drop ``source_content_id`` and leave a homework no import owns, which the
    course-repository path refuses on its next pull with ``homework_slug_collision`` --
    and would drop the imported instructions Markdown, its source path, and the unit
    links that resolve against it.  The module binding never moves either.
    """

    if slug_match is not None and slug_match.pk != paired.pk:
        existing.pop(slug_match.slug, None)
        slug_match.delete()
    return paired


def _target_slug(
    homework: Homework,
    cmp_slug: str,
    *,
    is_modules: bool,
    repository_slug_for: Mapping[str, str],
) -> str:
    """The slug a paired row should carry after this import.

    On a modules or shared cohort a repository-owned row keeps its repository slug --
    the encrypted answers and published URLs are bound to it, and the course-repository
    import refuses to change it once learners submitted.  A row an earlier import
    renamed to CMP's slug goes back to the repository slug its reviewed override names.
    Everything else takes CMP's slug, as before.
    """

    if not is_modules or homework.source_content_id is None:
        return cmp_slug
    if homework.slug == cmp_slug:
        return repository_slug_for.get(cmp_slug, cmp_slug)
    return homework.slug


def _rename(
    homework: Homework,
    slug: str,
    existing: dict[str, Homework],
    rebound: list[tuple[str, str, str]],
) -> None:
    previous_slug = homework.slug
    if previous_slug == slug:
        return
    if slug in existing and existing[slug].pk != homework.pk:
        _refuse("homework-slug-target-occupied")
    existing.pop(previous_slug, None)
    homework.slug = slug
    homework.save(update_fields=["slug"])
    existing[slug] = homework
    for module in Module.objects.filter(terminal_homework=homework):
        rebound.append((module.slug, previous_slug, slug))


def _import_cohort(
    connection: sqlite3.Connection,
    row: Any,
    cohort: Cohort,
    homework_slug_overrides: Mapping[str, Mapping[str, str]] = MappingProxyType({}),
    project_slug_overrides: Mapping[str, Mapping[str, str]] = MappingProxyType({}),
) -> CohortReport:
    _apply(cohort, _values(row, _COHORT_FIELDS))
    _import_explicit_start(cohort, row, "cohort")
    course_id = row["id"]

    homework_rows = _rows(
        connection,
        "SELECT * FROM courses_homework WHERE course_id = ? ORDER BY id",
        (course_id,),
    )
    is_modules = cohort.curriculum_format != Cohort.CurriculumFormat.LEGACY
    existing = {row.slug: row for row in Homework.objects.filter(course=cohort)}
    by_pk = {row.pk: row for row in existing.values()}
    pairings = homework_pairings()
    claimed = {
        int(source["id"]): by_pk[pairings[int(source["id"])]]
        for source in homework_rows
        if pairings.get(int(source["id"])) in by_pk
    }
    claimed_pks = {homework.pk for homework in claimed.values()}
    # Only a repository row CMP has no slug for can stand in for a CMP row, so a title
    # already claimed by a matching slug is never reused.
    superseding = {row["slug"] for row in homework_rows}
    by_title = {
        row.title: row
        for row in existing.values()
        if row.slug not in superseding and row.title and row.pk not in claimed_pks
    }
    # A handful of courses declare a repository homework slug CMP does not agree with,
    # and whose title no longer matches CMP's exactly either.  ``course_overrides`` is
    # the small, reviewed correction the caller supplies for exactly that -- see
    # ``scripts/prod/import_cmp_content.py`` -- keyed by the repository's own slug,
    # valued by the CMP slug it actually means.  It never guesses a pairing that title
    # matching wouldn't also catch on its own; it only covers the reviewed cases.
    course_overrides = homework_slug_overrides.get(cohort.course.slug, {})
    repository_slug_for = {cmp_slug: repo_slug for repo_slug, cmp_slug in course_overrides.items()}
    by_corrected_slug = {
        course_overrides[row.slug]: row
        for row in existing.values()
        if row.slug in course_overrides
        and row.slug not in superseding
        and row.pk not in claimed_pks
    }

    written = 0
    questions_written = 0
    questions_retained = 0
    repository_questions_kept = 0
    targets: set[int] = set()
    rebound: list[tuple[str, str, str]] = []
    unpaired_cmp: list[str] = []
    for source in homework_rows:
        source_id = int(source["id"])
        slug = str(source["slug"])
        values = _values(source, _HOMEWORK_FIELDS)
        slug_match = existing.get(slug)
        homework = claimed.get(source_id)
        if homework is None:
            paired = _repository_pair(by_title, values["title"], slug_match)
            if paired is None:
                paired = _repository_pair(by_corrected_slug, slug, slug_match)
            if paired is not None:
                homework = _adopt_repository_row(paired, slug_match, existing)
            else:
                homework = slug_match
        if homework is None:
            homework = Homework.objects.create(course=cohort, slug=slug, **values)
            existing[slug] = homework
            if is_modules:
                unpaired_cmp.append(slug)
        else:
            _rename(
                homework,
                _target_slug(
                    homework,
                    slug,
                    is_modules=is_modules,
                    repository_slug_for=repository_slug_for,
                ),
                existing,
                rebound,
            )
            _apply(homework, values)
        _import_explicit_start(homework, source, "homework")
        _record_pairing(source_id, homework.pk)
        targets.add(homework.pk)
        written += 1
        question_count, retained_count, kept_count = _reconcile_questions(
            connection, source["id"], homework, keep_repository_questions=is_modules
        )
        questions_written += question_count
        questions_retained += retained_count
        repository_questions_kept += kept_count

    leftover = Homework.objects.filter(course=cohort).exclude(pk__in=targets)
    if is_modules:
        # A modules cohort has two legitimate authors.  CMP owns the content of the
        # assignments it has; it does not assert that an assignment it lacks does not
        # exist, and deleting a repository-authored homework would strip its module's
        # page.  Report the divergence instead of resolving it by deletion.
        removed = 0
        unpaired_repository = tuple(sorted(leftover.values_list("slug", flat=True)))
    else:
        # CMP is the whole source for a legacy cohort, so a homework it does not have is
        # seed copy.  No module can be bound to one: legacy cohorts have no modules.
        removed = leftover.delete()[0]
        unpaired_repository = ()

    project_rows = _rows(
        connection,
        "SELECT * FROM courses_project WHERE course_id = ? ORDER BY id",
        (course_id,),
    )
    projects_written = 0
    project_targets: set[int] = set()
    projects_folded = 0
    repository_project_for = {
        cmp_slug: repo_slug
        for repo_slug, cmp_slug in project_slug_overrides.get(cohort.course.slug, {}).items()
    }
    stored_projects = project_pairings()
    for source in project_rows:
        source_id = int(source["id"])
        slug = str(source["slug"])
        values = _values(source, _PROJECT_FIELDS)
        project = Project.objects.filter(
            course=cohort, pk=stored_projects.get(source_id, -1)
        ).first()
        if project is None and is_modules and slug in repository_project_for:
            project = Project.objects.filter(
                course=cohort, slug=repository_project_for[slug]
            ).first()
        if project is not None and project.slug != slug:
            # A repository-authored attempt CMP knows under another slug (reviewed in
            # ``project_slug_overrides``).  The repository owns its slug, title,
            # description, deadlines and peers, so CMP contributes only the rest; a
            # CMP-slugged duplicate an earlier import created is folded away unless
            # learners submitted to it.
            duplicate = Project.objects.filter(course=cohort, slug=slug).first()
            if (
                duplicate is not None
                and not ProjectSubmission.objects.filter(project=duplicate).exists()
            ):
                duplicate.delete()
                projects_folded += 1
            _apply(
                project,
                {
                    name: value
                    for name, value in values.items()
                    if name not in _REPOSITORY_PROJECT_FIELDS
                },
            )
            _record_pairing(source_id, project.pk, PROJECT_PAIRING_TABLE)
        else:
            project, created = Project.objects.get_or_create(
                course=cohort, slug=slug, defaults=values
            )
            if not created:
                _apply(project, values)
        _import_explicit_start(project, source, "project")
        project_targets.add(project.pk)
        projects_written += 1
    leftover_projects = Project.objects.filter(course=cohort).exclude(pk__in=project_targets)
    if is_modules:
        # The homework rule above, for the same reason: a repository-authored project
        # carries the repository's criteria set and module placement, and CMP lacking
        # its slug does not assert it does not exist.  Report, never delete.
        projects_removed = 0
        unpaired_repository_projects = tuple(
            sorted(leftover_projects.values_list("slug", flat=True))
        )
    else:
        projects_removed = leftover_projects.delete()[0]
        unpaired_repository_projects = ()

    criteria_written, criteria_retained = _reconcile_criteria(connection, course_id, cohort)

    return CohortReport(
        cohort_slug=cohort.slug,
        homework_written=written,
        homework_removed=removed,
        questions_written=questions_written,
        questions_retained=questions_retained,
        repository_questions_kept=repository_questions_kept,
        projects_written=projects_written,
        projects_removed=projects_removed + projects_folded,
        criteria_written=criteria_written,
        criteria_retained=criteria_retained,
        rebound_modules=tuple(rebound),
        unpaired_cmp_homework=tuple(sorted(unpaired_cmp)),
        unpaired_repository_homework=unpaired_repository,
        unpaired_repository_projects=unpaired_repository_projects,
    )


def _reconcile_questions(
    connection: sqlite3.Connection,
    homework_id: Any,
    homework: Homework,
    *,
    keep_repository_questions: bool = False,
) -> tuple[int, int, int]:
    """Reconcile questions without deleting imported learner answers.

    Text plus source order is the same identity used by the history importer. Reusing
    those rows keeps answer foreign keys stable on replay, including the one reviewed
    source homework that repeats a question verbatim.

    On a modules or shared cohort a repository-authored question CMP does not have is
    kept and counted rather than deleted, for the reason a repository homework is: CMP
    lacking it does not assert it does not exist, and deleting it only made the next
    repository pull re-create it.  Returns (written, retained, repository kept).
    """

    rows = _rows(
        connection,
        "SELECT * FROM courses_question WHERE homework_id = ? ORDER BY id",
        (homework_id,),
    )
    available: dict[str, list[Question]] = {}
    for question in Question.objects.filter(homework=homework).order_by("pk"):
        available.setdefault(question.text, []).append(question)

    for source in rows:
        values = _values(source, _QUESTION_FIELDS)
        matches = available.get(values["text"], [])
        if matches:
            question = matches.pop(0)
            _apply(question, values)
        else:
            Question.objects.create(homework=homework, **values)

    retained = 0
    kept = 0
    for leftovers in available.values():
        for question in leftovers:
            if keep_repository_questions and question.source_content_id is not None:
                kept += 1
            elif Answer.objects.filter(question=question).exists():
                retained += 1
            else:
                question.delete()
    return len(rows), retained, kept


def _reconcile_criteria(
    connection: sqlite3.Connection, course_id: Any, cohort: Cohort
) -> tuple[int, int]:
    """Reconcile criteria by the history importer's documented natural key.

    A criterion description is unique within a source cohort. Keeping the target row
    preserves criteria responses, evaluation scores, and project assignments. A stale
    referenced row is retained and counted instead of turning source drift into learner
    history deletion or a verbose ``ProtectedError`` containing record representations.
    """

    rows = _rows(
        connection,
        "SELECT * FROM courses_reviewcriteria WHERE course_id = ? ORDER BY id",
        (course_id,),
    )
    available: dict[str, list[ReviewCriteria]] = {}
    for criterion in ReviewCriteria.objects.filter(course=cohort).order_by("pk"):
        available.setdefault(criterion.description, []).append(criterion)

    for source in rows:
        values = _values(source, _CRITERIA_FIELDS)
        matches = available.get(values["description"], [])
        if matches:
            criterion = matches.pop(0)
            _apply(criterion, values)
        else:
            ReviewCriteria.objects.create(course=cohort, **values)

    retained = 0
    for leftovers in available.values():
        for criterion in leftovers:
            try:
                criterion.delete()
            except ProtectedError:
                retained += 1
    return len(rows), retained
