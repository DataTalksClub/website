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
* **CMP owns homework identity.**  A homework's slug is whatever CMP says it is, copied
  verbatim, including on the modules-format cohorts whose repositories declare a
  different one.  Nothing is derived, mapped or rewritten.

  A modules-format cohort binds its homework to repository-authored modules through
  ``Module.terminal_homework``, so adopting CMP's slug means re-pointing that binding.
  The pairing is read from data both sides already publish -- the slug when they agree,
  otherwise an exact title match -- and anything that does not pair is **left unbound
  and reported**.  Guessing by ordinal position would misattach CMP's ``dlt`` workshop
  to the sixth LLM module, which renders as a page that looks fine and is wrong.

  A paired row is *renamed*, never replaced.  The row stays the repository's, keeping
  ``source_content_id``, the imported instructions Markdown, its source path, its units
  and its module binding; only the slug and CMP's own fields change.  Replacing it left
  a homework no import owned, which is what the course-repository path refuses on its
  next pull with ``homework_slug_collision``, and which re-created the repository row
  beside it so the reconciliation undid itself on every replay.

  A pairing whose repository title no longer matches CMP's exactly (a stale ``[DRAFT]``
  marker, a title rewritten before launch) is left unbound by title alone -- guessing by
  near-enough text is exactly the wrong-attachment risk this reconciliation refuses to
  take.  ``homework_slug_overrides`` is the small, reviewed correction for that: an
  explicit ``{repository_slug: cmp_slug}`` pair the caller already knows names the same
  assignment, used only as a fallback when title matching finds nothing.

Running it twice is a no-op: every write is keyed on a natural key, and a homework's
questions are replaced as a set.
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

from courses.services.course_family_identity import (
    UnparseableEditionSlug,
    family_and_year_from_edition_slug,
    family_title_from_edition_title,
)
from courses.models import (
    Cohort,
    Homework,
    Module,
    Project,
    Question,
    ReviewCriteria,
    Submission,
)
from courses.models.cohort import Course, RegistrationCampaign

__all__ = [
    "CampaignReport",
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
    projects_written: int = 0
    projects_removed: int = 0
    criteria_written: int = 0
    rebound_modules: tuple[tuple[str, str, str], ...] = ()
    unpaired_cmp_homework: tuple[str, ...] = ()
    unpaired_repository_homework: tuple[str, ...] = ()


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
            "projects_written": sum(row.projects_written for row in self.imported),
            "projects_removed": sum(row.projects_removed for row in self.imported),
            "criteria_written": sum(row.criteria_written for row in self.imported),
            "modules_rebound": sum(len(row.rebound_modules) for row in self.imported),
            "unpaired_cmp_homework": sorted(
                slug for row in self.imported for slug in row.unpaired_cmp_homework
            ),
            "unpaired_repository_homework": sorted(
                slug for row in self.imported for slug in row.unpaired_repository_homework
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
    if value is None and name in _NULLABLE_TEXT_FIELDS:
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
    family_slug_overrides: Mapping[str, str] = MappingProxyType({}),
    homework_slug_overrides: Mapping[str, Mapping[str, str]] = MappingProxyType({}),
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

    connection = _readonly(source_db)
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
                    missing.append(slug)
                    dependent[slug] = _dependent_row_total(connection, row["id"])
                    continue
                local[slug] = cohort
                created.append(slug)
            if identity is not None:
                family_slug, _year = identity
                if cohort.course.slug != family_slug:
                    _refuse("cohort-family-mismatch")
            with transaction.atomic():
                imported.append(
                    _import_cohort(connection, row, cohort, homework_slug_overrides)
                )

        campaigns, unlinked = _import_registration_campaigns(
            connection, source_cohorts, local, cohort_slugs
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


def _family_and_year(
    slug: str, overrides: Mapping[str, str]
) -> tuple[str, int] | None:
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
        title = family_title_from_edition_title(str(row["title"] or "")) or family_slug.replace(
            "-", " "
        ).title()
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
) -> tuple[tuple[CampaignReport, ...], tuple[str, ...]]:
    """Copy CMP's registration campaign *definitions*, keyed by slug.

    A campaign says what is open, under which public slug, with which copy, and which
    cohort it promotes.  The learners who registered through it are a different table and
    are never read.  ``current_course`` is resolved through the local catalogue, so a
    campaign whose cohort this database does not hold arrives unlinked and is reported
    rather than silently pointing at nothing.
    """

    slug_by_source_id = {row["id"]: str(row["slug"]) for row in source_cohorts}
    rows = _rows(connection, "SELECT * FROM courses_registrationcampaign ORDER BY slug")
    existing = {campaign.slug: campaign for campaign in RegistrationCampaign.objects.all()}

    reports: list[CampaignReport] = []
    unlinked: list[str] = []
    for source in rows:
        slug = str(source["slug"])
        promoted_slug = slug_by_source_id.get(source["current_course_id"], "")
        if cohort_slugs is not None and promoted_slug not in cohort_slugs:
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
    slug: str,
    values: Mapping[str, Any],
    rebound: list[tuple[str, str, str]],
    existing: dict[str, Homework],
) -> Homework:
    """Give the repository's row CMP's slug and content, in place.

    CMP owns homework identity, so the slug becomes CMP's.  The row itself stays the
    repository's: replacing it would drop ``source_content_id`` and leave a homework no
    import owns, which the course-repository path refuses on its next pull with
    ``homework_slug_collision`` -- and would drop the imported instructions Markdown, its
    source path, and the unit links that resolve against it.  Renaming keeps the module
    binding as well, because the foreign key never moves.
    """

    if slug_match is not None:
        existing.pop(slug_match.slug, None)
        slug_match.delete()
    previous_slug = paired.slug
    existing.pop(previous_slug, None)
    paired.slug = slug
    paired.save(update_fields=["slug"])
    _apply(paired, values)
    if previous_slug != slug:
        for module in Module.objects.filter(terminal_homework=paired):
            rebound.append((module.slug, previous_slug, slug))
    return paired


def _import_cohort(
    connection: sqlite3.Connection,
    row: Any,
    cohort: Cohort,
    homework_slug_overrides: Mapping[str, Mapping[str, str]] = MappingProxyType({}),
) -> CohortReport:
    _apply(cohort, _values(row, _COHORT_FIELDS))
    course_id = row["id"]

    homework_rows = _rows(
        connection,
        "SELECT * FROM courses_homework WHERE course_id = ? ORDER BY id",
        (course_id,),
    )
    is_modules = cohort.curriculum_format != Cohort.CurriculumFormat.LEGACY
    existing = {row.slug: row for row in Homework.objects.filter(course=cohort)}
    # Only a repository row CMP has no slug for can stand in for a CMP row, so a title
    # already claimed by a matching slug is never reused.
    superseding = {row["slug"] for row in homework_rows}
    by_title = {
        row.title: row for row in existing.values() if row.slug not in superseding and row.title
    }
    # A handful of courses declare a repository homework slug CMP does not agree with
    # (for example ai-dev-tools-zoomcamp's modules-3/4 files, still ``hw03``/``hw04``
    # from before the assignments were finalized and re-titled for CMP, so their titles
    # no longer match exactly either).  ``course_overrides`` is the small, reviewed
    # correction the caller supplies for exactly that -- see
    # ``scripts/prod/import_cmp_content.py`` -- keyed by the repository's own irregular
    # slug, valued by the CMP slug it actually means.  It never guesses a pairing that
    # title matching wouldn't also catch on its own; it only covers the reviewed cases.
    course_overrides = homework_slug_overrides.get(cohort.course.slug, {})
    by_corrected_slug = {
        course_overrides[row.slug]: row
        for row in existing.values()
        if row.slug in course_overrides and row.slug not in superseding
    }

    written = 0
    questions_written = 0
    source_slugs: set[str] = set()
    rebound: list[tuple[str, str, str]] = []
    unpaired_cmp: list[str] = []
    for source in homework_rows:
        slug = str(source["slug"])
        source_slugs.add(slug)
        values = _values(source, _HOMEWORK_FIELDS)
        homework = existing.get(slug)
        paired = _repository_pair(by_title, values["title"], homework)
        if paired is None:
            paired = _repository_pair(by_corrected_slug, slug, homework)
        if paired is not None:
            homework = _adopt_repository_row(paired, homework, slug, values, rebound, existing)
        elif homework is not None:
            # Slugs already agree, so the module binding needs no repair.
            _apply(homework, values)
        else:
            homework = Homework.objects.create(course=cohort, slug=slug, **values)
            if is_modules:
                unpaired_cmp.append(slug)
        existing[slug] = homework
        written += 1
        questions_written += _replace_questions(connection, source["id"], homework)

    leftover = Homework.objects.filter(course=cohort).exclude(slug__in=source_slugs)
    if is_modules:
        # A modules cohort has two legitimate authors.  CMP owns the identity and content
        # of the assignments it has; it does not assert that an assignment it lacks does
        # not exist, and deleting a repository-authored homework would strip its module's
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
        rebound_modules=tuple(rebound),
        unpaired_cmp_homework=tuple(sorted(unpaired_cmp)),
        unpaired_repository_homework=unpaired_repository,
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
