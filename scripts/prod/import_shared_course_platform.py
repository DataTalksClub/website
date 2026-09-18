#!/usr/bin/env python3
"""P6: copy the site course platform into the shared curriculum/coursework tables.

Declared ``one-time`` and meaning it: the site ``courses.*`` tables are frozen
history at the moment of the D5.1 cutover, this script reads them once, and the
shared ``cb_curriculum``/``cb_coursework`` tables it writes have no other
writer (see the D5.1 note in ``website/settings/base.py``).  Re-running is
still safe -- every write reconciles on the natural key its family has, so a
replay reports the same counts and changes nothing.

What migrates, and by whose decision
------------------------------------

The field-level mapping and the gap decisions live in
``_docs/architecture/course-platform-shared-apps-mapping.md``; the numbered
decisions below cite that document's decision record, which the owner can veto
item by item before the D5.2 freeze weekend.  In brief:

- Courses, cohorts, the shared current-curriculum graph, enrollments,
  certificates, read progress and every coursework family copy across.
  Certificates are promoted from ``Enrollment.certificate_url`` to
  ``curriculum.Certificate`` rows with the url preserved byte-for-byte.
- Only the shared graph is curriculum source material.  A course whose
  teaching material still sits in cohort-owned ``Module``/``Unit`` rows that
  the ``migrate_shared_curriculum`` backfill cannot have absorbed refuses the
  whole run, naming the course and the backfill that fixes it (decision 12).
  A fully backfilled cohort keeps its old cohort-owned rows for audit; those
  do not block the import, because the rows the site serves from are the
  shared placements the backfill wrote.
- Provenance converts: a complete site provenance set becomes a package set
  with ``source_content_id = uuid5(PROVENANCE_NAMESPACE, site_id)``; anything
  incomplete migrates with all-None provenance, as the package's
  all-or-nothing constraint requires (decision 7).
- The gaps stay site-side and are counted, never silently dropped:
  terminal-homework bindings (2), project flow placements (3), module links
  and lesson code sources (4), cohort-level overrides (5), shared curriculum
  assets (6), course people (8), and the module/lesson ``summary`` text the
  package has no column for (decision 17).
- ``delivery_mode`` ``live`` becomes package ``mode`` ``cohort`` in this
  mapping, not in a later datafix (9); a site ``visible`` course is
  ``published`` and an invisible one ``draft`` (10).
- Unpublished shared modules and unpublished or retired shared lessons are
  skipped and counted, and the count equality is asserted on that migratable
  subset (decision 11); derived rows -- statistics, leaderboard positions --
  are copied verbatim, never recomputed (decision 13).
- The package's pooled (self-paced) peer-review machinery has no site
  counterpart, so decision 18 answers its three fields explicitly rather than
  by omission: ``ProjectSubmission.review_state`` is derived from the site
  project's state, because in deadline mode it is exactly that mirror;
  ``Project.pooled_review_window_days`` and ``PeerReview.batch`` are left at
  their package defaults, because a pooled window and a pooled batch are
  things the site never had and this import must not invent.

Three refusals, all before any family is written
------------------------------------------------

1. ``shared_backfill_required`` -- decision 12, bounded and named above.
2. ``unknown_delivery_mode`` -- a cohort whose ``delivery_mode`` is neither
   ``live`` nor ``self_paced``.  Inventing a mapping in the importer is how a
   silent value corruption ships; the run stops and names the value instead.
3. ``mapping_coverage_drift`` -- a concrete field on either side of any named
   model pair that the mapping does not name (refusal 3 of the runbook).  The
   mapping is data (``_mapping`` below) and is checked against the live
   models on every run, so model drift must fail the run, never drop a field
   quietly.

Dry run by default
------------------

Without ``--apply`` the run computes everything inside a rolled-back
transaction and prints the report it would have produced: the per-family
counts, the counted gaps, and the count-equality verification.  The report is
the rehearsal evidence the D5.1 verification asks for ("counts equal"); an
operator compares two runs -- dry, then applied -- and expects the same
numbers.

    uv run --frozen python scripts/prod/import_shared_course_platform.py \
        --database .tmp/local.sqlite3 --apply

``--deployment-target`` names a reviewed deployed database instead, with the
same double opt-in every ``scripts/prod`` entry point shares.  The production
run itself is an operator procedure, not a code path: rehearse on the
development copy first, verify the counts printed here, and only then aim the
same command at production.

Rows with no natural key on either side (submissions, criteria, complaints)
reconcile on the strongest key their family has, up to full row values.  Two
genuinely indistinguishable site rows -- same homework, student and timestamp;
same criterion definition twice -- cannot both land, so the final
count-equality verification refuses the run rather than silently merging them:
a refusal is a data question for the operator, a silent merge is data loss.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.prod.target import add_target_arguments, configure_target  # noqa: E402

SYNC_MODEL = "one-time"
BOOTSTRAPS_EMPTY_DATABASE = False

#: Decision 7: site ``source_content_id`` strings convert to package UUIDs
#: through this one fixed namespace.  Changing it would orphan every
#: already-migrated row on a replay, so it is a constant of the migration.
PROVENANCE_NAMESPACE = uuid.uuid5(
    uuid.NAMESPACE_URL, "dtc-website/course-platform-source-content-ids"
)

#: Bounded reports: refusals name conditions and at most this many rows,
#: never row contents (the same posture as the shared-curriculum backfill).
MAX_NAMED_ROWS = 20

_COURSE_STATUS_VISIBLE = "published"
_COURSE_STATUS_HIDDEN = "draft"
_MODE_LIVE_TO_COHORT = {"live": "cohort", "self_paced": "self_paced"}

#: Decision 18: the package's per-submission peer-review lifecycle
#: (``ProjectSubmission.review_state``, package issue C5.2f) is, in deadline mode, a pure
#: mirror of the whole-project ``Project.state`` -- which is the only thing the site records.
#: This table is the package's own mirror, copied from the ``cb_coursework`` migration that
#: backfills the field for rows that predate it: ``COMPLETED -> SCORED``,
#: ``PEER_REVIEWING -> IN_REVIEW``, and everything else stays at the field's
#: ``AWAITING_ASSIGNMENT`` default.  Rows this import creates arrive after that migration has
#: run, so the derivation has to happen here or a migrated submission of a finished project
#: would read as never assigned -- and drop out of the package leaderboard and project
#: statistics, both of which now filter on ``review_state == SCORED``.
_PROJECT_STATE_TO_REVIEW_STATE = {"CO": "SC", "PR": "IR"}
_REVIEW_STATE_AWAITING_ASSIGNMENT = "AW"


class CoursePlatformImportError(RuntimeError):
    """A safe refusal that carries a condition code, never a source value."""

    #: Subclasses may name up to :data:`MAX_NAMED_ROWS` courses; the entry
    #: point lifts them into the printed report.
    course_slugs: tuple[str, ...] = ()


class SharedBackfillRequired(CoursePlatformImportError):
    """Decision 12: cohort-owned teaching material with no shared graph."""

    def __init__(self, course_slugs: Iterable[str]):
        slugs = tuple(sorted(course_slugs))
        super().__init__("shared_backfill_required")
        self.course_slugs = slugs


class UnknownDeliveryMode(CoursePlatformImportError):
    """A cohort's ``delivery_mode`` is neither ``live`` nor ``self_paced``."""

    def __init__(self, named: Iterable[str]):
        self.named = tuple(sorted(named))
        super().__init__("unknown_delivery_mode")


class MappingCoverageDrift(CoursePlatformImportError):
    """A model grew a field the mapping does not name, on either side."""

    def __init__(self, models: Iterable[str]):
        self.models = tuple(sorted(models))[:MAX_NAMED_ROWS]
        super().__init__("mapping_coverage_drift")


class SelfPacedCohortNotUnique(CoursePlatformImportError):
    """The package allows one self-paced cohort per course; the site allowed many."""

    def __init__(self, course_slugs: Iterable[str]):
        slugs = tuple(sorted(course_slugs))
        super().__init__("self_paced_cohort_not_unique")
        self.course_slugs = slugs


class CountMismatch(CoursePlatformImportError):
    """The post-copy equality verification failed for at least one family."""

    def __init__(self, families: Iterable[str]):
        self.families = tuple(sorted(families))[:MAX_NAMED_ROWS]
        super().__init__("count_mismatch")


def _bounded(slugs: Iterable[str]) -> list[str]:
    return sorted(set(slugs))[:MAX_NAMED_ROWS]


def _package_provenance(site_row: Any) -> dict[str, Any]:
    """Convert one site provenance set, all-or-nothing (decision 7).

    Completeness follows the site's own all-or-nothing constraint: the four
    shared columns move together, and a row the site accepted with provenance
    carries its identity fields complete too.  A blank counts as absent -- the
    package must never receive a half-populated set.
    """

    complete = all(
        getattr(site_row, field)
        for field in (
            "source_content_id",
            "source_path",
            "source_commit_sha",
            "source_checksum",
        )
    )
    if not complete:
        return {
            "source_content_id": None,
            "source_path": None,
            "source_commit_sha": None,
            "source_checksum": None,
        }
    return {
        "source_content_id": uuid.uuid5(PROVENANCE_NAMESPACE, site_row.source_content_id),
        "source_path": site_row.source_path,
        "source_commit_sha": site_row.source_commit_sha,
        "source_checksum": site_row.source_checksum,
    }


class _Report:
    """Per-family counters plus the flat gap ledger the report prints."""

    def __init__(self) -> None:
        self.counts: dict[str, dict[str, int]] = {}
        self.gaps: dict[str, int] = {}
        self.named: dict[str, list[str]] = {}

    def family(self, name: str, site_total: int) -> dict[str, int]:
        entry = self.counts.setdefault(name, {"site": site_total, "migrated": 0, "skipped": 0})
        entry["site"] = site_total
        return entry

    def migrated(self, name: str) -> None:
        self.counts[name]["migrated"] += 1

    def skipped(self, name: str, reason: str, slug: str | None = None) -> None:
        self.counts[name]["skipped"] += 1
        key = f"{name}:{reason}"
        self.gaps[key] = self.gaps.get(key, 0) + 1
        if slug is not None and len(self.named.get(key, ())) < MAX_NAMED_ROWS:
            self.named.setdefault(key, []).append(slug)

    def gap(self, key: str) -> None:
        self.gaps[key] = self.gaps.get(key, 0) + 1

    def as_dict(self) -> dict[str, Any]:
        report: dict[str, Any] = {"counts": self.counts, "gaps": self.gaps}
        if self.named:
            report["skipped_identifiers"] = self.named
        return report


def _refuse_mapping_drift() -> None:
    """Refusal 3: a field the mapping does not name fails the run."""

    from django.apps import apps

    problems: list[str] = []
    for (site_label, package_label, site_only), (
        site_fields,
        package_written,
        package_defaults,
    ) in _mapping().items():
        site_app, site_name = site_label.split(".")
        site_model = apps.get_model(site_app, site_name)
        unnamed_site = {
            field.name for field in site_model._meta.concrete_fields if not field.primary_key
        } - set(site_fields)
        if unnamed_site:
            problems.append(f"{site_label}: {', '.join(sorted(unnamed_site))}")
        if site_only:
            # A pair that writes no package row: its package side is checked
            # through the family that really writes that model.
            continue
        package_app, package_name = package_label.split(".")
        package_model = apps.get_model(package_app, package_name)
        unnamed_package = {
            field.name
            for field in package_model._meta.concrete_fields
            if not field.primary_key
            and not (getattr(field, "auto_now", False) or getattr(field, "auto_now_add", False))
            and field.name not in package_written
            and field.name not in package_defaults
        }
        if unnamed_package:
            problems.append(f"{package_label}: {', '.join(sorted(unnamed_package))}")
    if problems:
        raise MappingCoverageDrift(problems)


def _refuse_without_shared_graph() -> None:
    """Decision 12: refuse the run naming every still-bespoke course.

    A course is still bespoke when a ``modules``-format cohort of its serves
    cohort-owned rows the shared backfill cannot have absorbed: either the
    cohort has no shared placements at all (never backfilled), or one of its
    modules carries no stable source id (the backfill matches rows by stable
    source content ID, so such a row can never move).  A fully backfilled
    cohort keeps its old cohort-owned rows for audit -- those do not block
    the import, because what the site serves from is the shared placements
    the backfill wrote.
    """

    from courses.models import Cohort

    still_bespoke = set()
    module_cohorts = (
        Cohort.objects.filter(curriculum_format=Cohort.CurriculumFormat.MODULES)
        .filter(modules__isnull=False)
        .distinct()
    )
    for cohort in module_cohorts:
        if not cohort.shared_module_placements.exists():
            still_bespoke.add(cohort.course.slug)
        elif cohort.modules.filter(source_content_id__isnull=True).exists():
            still_bespoke.add(cohort.course.slug)
    if still_bespoke:
        raise SharedBackfillRequired(still_bespoke)


def _refuse_unknown_delivery_modes() -> None:
    """Stop and name any delivery mode the mapping does not cover.

    ``_MODE_LIVE_TO_COHORT[...]`` below must never meet a value it did not
    expect: inventing a mapping in the importer is how a silent value
    corruption ships, so the run refuses first and names the cohorts.
    """

    from courses.models import Cohort

    unknown = Cohort.objects.exclude(delivery_mode__in=_MODE_LIVE_TO_COHORT)
    if not unknown.exists():
        return
    named = [
        f"{cohort.course.slug}/{cohort.identifier}:{cohort.delivery_mode}"
        for cohort in unknown.select_related("course").order_by("pk")[:MAX_NAMED_ROWS]
    ]
    raise UnknownDeliveryMode(named)


def _refuse_ambiguous_self_paced() -> None:
    """The package keeps one self-paced cohort per course; the site allowed many."""

    from django.db.models import Count

    from courses.models import Cohort

    duplicated = (
        Cohort.objects.filter(delivery_mode="self_paced")
        .values("course__slug")
        .annotate(total=Count("pk"))
        .filter(total__gt=1)
        .values_list("course__slug", flat=True)
    )
    offenders = list(duplicated)
    if offenders:
        raise SelfPacedCohortNotUnique(offenders)


# --------------------------------------------------------------------------
# Refusal 3's data: the mapping, named, checked against the live models.
#
# Each entry is one (site model, package model) pair.  ``site_fields`` names
# every concrete site field with the verdict the mapping gives it; for pairs
# that write package rows, ``package_written`` names every concrete package
# field the import writes and ``package_defaults`` every one deliberately
# left at its default (Django-managed auto fields are exempt).  ``site_only``
# pairs write no package row -- the bespoke graph, the recorded stays -- and
# are named so those site models still cannot drift silently; their package
# side is checked through the pair that really writes that model.
# --------------------------------------------------------------------------

_COPIED = "copied across"
_PROV = "provenance set (decision 7)"
_STAYS = "stays site-side"

_PROV_WRITTEN = frozenset(
    {"source_content_id", "source_path", "source_commit_sha", "source_checksum"}
)


def _mapping() -> dict[
    tuple[str, str, bool], tuple[dict[str, str], frozenset[str], frozenset[str]]
]:
    """Build the mapping registry against the live site models."""

    from courses import models as site

    prov = {
        "source_content_id": _PROV,
        "source_path": _PROV,
        "source_commit_sha": _PROV,
        "source_checksum": _PROV,
    }

    def concrete(model: Any, exclude: set[str]) -> dict[str, str]:
        return {
            field.name: _COPIED
            for field in model._meta.concrete_fields
            if field.name not in exclude
        }

    def written(model: Any, exclude: set[str]) -> frozenset:
        return frozenset(
            field.name for field in model._meta.concrete_fields if field.name not in exclude
        )

    mapping: dict[
        tuple[str, str, bool],
        tuple[dict[str, str], frozenset[str], frozenset[str]],
    ] = {}

    mapping[("courses.Course", "cb_curriculum.Course", False)] = (
        {
            "slug": _COPIED,
            "title": _COPIED,
            "description": _COPIED,
            "github_repo_url": _COPIED,
            "docs_url": _COPIED,
            "faq_document_url": "copied as faq_url",
            "social_media_hashtag": "copied as hashtag",
            "visible": "copied; also decides status (decision 10)",
            "source_stable_id": "consumed by the provenance conversion; no package column",
            **prov,
            "starting_point": _STAYS,
            "prerequisites": _STAYS,
            "weekly_commitment": _STAYS,
            "progression": _STAYS,
            "outcome": _STAYS,
        },
        frozenset(
            {
                "slug",
                "title",
                "description",
                "github_repo_url",
                "docs_url",
                "faq_url",
                "hashtag",
                "visible",
                "status",
                *_PROV_WRITTEN,
            }
        ),
        # description_html is not written by hand: Course.save() renders it
        # from description.  The remaining columns are package-only: banner
        # urls the site banner generator owns, the access levels DTC has no
        # tiers for, and the JSON columns the site keeps as rows.
        frozenset(
            {
                "description_html",
                "cover_image_url",
                "auto_banner_url",
                "custom_banner_url",
                "required_level",
                "default_unit_required_level",
                "discussion_url",
                "tags",
                "testimonials",
            }
        ),
    )

    mapping[("courses.Cohort", "cb_curriculum.Cohort", False)] = (
        {
            "course": _COPIED,
            "identifier": "copied as slug; the legacy edition slug is the fallback",
            "delivery_mode": "copied as mode; live->cohort in this mapping (decision 9)",
            "title": _COPIED,
            "start_date": _COPIED,
            "end_date": _COPIED,
            "registration_url": _COPIED,
            "social_media_hashtag": "copied as hashtag",
            "first_homework_scored": _COPIED,
            "finished": _COPIED,
            "min_projects_to_pass": _COPIED,
            "homework_problems_comments_field": _COPIED,
            "project_passing_score": _COPIED,
            "visible": _COPIED,
            **prov,
            "slug": _STAYS,
            "uuid": _STAYS,
            "year": _STAYS,
            "curriculum_format": _STAYS,
            "curriculum_source": _STAYS,
            "shared_curriculum": _STAYS,
            "archive_notice_path": _STAYS,
            "archive_url": _STAYS,
            "archive_commit_sha": _STAYS,
            "description": _STAYS,
            "outcome": _STAYS,
            "promo_summary": _STAYS,
            "delivery_format": _STAYS,
            "github_repo_url": _STAYS,
            "students": "M2M through Enrollment; expressed as package enrollments",
        },
        frozenset(
            {
                "course",
                "slug",
                "title",
                "mode",
                "start_date",
                "end_date",
                "registration_url",
                "hashtag",
                "finished",
                "visible",
                "max_participants",
                "project_passing_score",
                "min_projects_to_pass",
                "first_homework_scored",
                "homework_problems_comments_field",
                *_PROV_WRITTEN,
            }
        ),
        frozenset(),
    )

    mapping[("courses.SharedModule", "cb_curriculum.Module", False)] = (
        {
            "curriculum": "copied as course",
            "position": "copied as sort_order",
            "slug": _COPIED,
            "title": _COPIED,
            "overview_markdown": "copied as overview",
            "overview_rendered_html": "copied as overview_html, byte-identical",
            "published": "skip filter (decision 11)",
            "summary": "stays site-side; no package column (decision 17)",
            "retired_at": "retired published modules migrate; no package column",
            **prov,
        },
        frozenset(
            {
                "course",
                "slug",
                "title",
                "sort_order",
                "overview",
                "overview_html",
                *_PROV_WRITTEN,
            }
        ),
        frozenset({"parent", "is_bonus", "available_after_days"}),
    )

    mapping[("courses.SharedLesson", "cb_curriculum.Unit", False)] = (
        {
            "module": _COPIED,
            "position": "copied as sort_order",
            "slug": _COPIED,
            "title": _COPIED,
            "content_markdown": "copied as body",
            "rendered_html": "copied as body_html, byte-identical",
            "video_url": _COPIED,
            "published": "skip filter (decision 11)",
            "retired_at": "skip filter (decision 11)",
            "summary": "stays site-side; no package column (decision 17)",
            "code_sources": "stays site-side; no package column (decision 4)",
            **prov,
        },
        frozenset(
            {
                "module",
                "slug",
                "title",
                "sort_order",
                "body",
                "body_html",
                "video_url",
                *_PROV_WRITTEN,
            }
        ),
        frozenset(
            {
                "kind",
                "session_position",
                "is_bonus",
                "homework",
                "homework_html",
                "timestamps",
                "is_preview",
                # C7.8 (v0.5.0) named where body_html comes from.  This import
                # writes body_html out of band, after save(), so the unit keeps
                # the default `markdown` and the package's own renderer stays
                # the declared source.  Nothing here supplies parser HTML.
                "body_html_source",
                "required_level",
                "available_after_days",
                "content_hash",
            }
        ),
    )

    mapping[("courses.CohortSharedModule", "cb_curriculum.CohortModule", False)] = (
        {
            "cohort": _COPIED,
            "shared_module": "copied as module",
            "position": "copied as sort_order",
            "terminal_homework": "stays site-side (decision 2)",
        },
        frozenset({"cohort", "module", "sort_order"}),
        frozenset(),
    )

    mapping[("courses.Enrollment", "cb_curriculum.Enrollment", False)] = (
        {
            "student": "copied as user",
            "course": "copied as cohort",
            "enrollment_date": "copied as enrolled_at, re-stamped after the write",
            "display_name": _COPIED,
            "display_on_leaderboard": _COPIED,
            "display_public_profile": _COPIED,
            "position_on_leaderboard": _COPIED,
            "certificate_name": _COPIED,
            "total_score": _COPIED,
            "certificate_url": _COPIED,
            "disable_learning_in_public": _COPIED,
        },
        frozenset(
            {
                "user",
                "cohort",
                "enrolled_at",
                "unenrolled_at",
                "source",
                "display_name",
                "display_on_leaderboard",
                "display_public_profile",
                "position_on_leaderboard",
                "disable_learning_in_public",
                "certificate_name",
                "total_score",
                "certificate_url",
            }
        ),
        frozenset(),
    )

    mapping[("courses.Enrollment", "cb_curriculum.Certificate", False)] = (
        {
            "certificate_url": "promoted to Certificate.url, byte-identical",
            "student": "covered by the enrollments family",
            "course": "covered by the enrollments family",
            "enrollment_date": "covered by the enrollments family",
            "display_name": "covered by the enrollments family",
            "display_on_leaderboard": "covered by the enrollments family",
            "display_public_profile": "covered by the enrollments family",
            "position_on_leaderboard": "covered by the enrollments family",
            "certificate_name": "covered by the enrollments family",
            "total_score": "covered by the enrollments family",
            "disable_learning_in_public": "covered by the enrollments family",
        },
        frozenset({"enrollment", "url"}),
        frozenset({"hash"}),
    )

    mapping[("courses.SharedLessonReadState", "cb_curriculum.UnitProgress", False)] = (
        {
            "user": _COPIED,
            "shared_lesson": "copied as unit",
            "read_at": "copied as completed_at",
        },
        frozenset({"user", "unit", "completed_at"}),
        frozenset(),
    )

    mapping[("courses.Homework", "cb_coursework.Homework", False)] = (
        {
            "course": "copied as cohort",
            "slug": _COPIED,
            "title": _COPIED,
            "description": _COPIED,
            "instructions_markdown": _COPIED,
            "instructions_source_path": _COPIED,
            "instructions_url": _COPIED,
            "due_date": _COPIED,
            "learning_in_public_cap": _COPIED,
            "homework_url_field": _COPIED,
            "time_spent_lectures_field": _COPIED,
            "time_spent_homework_field": _COPIED,
            "faq_contribution_field": _COPIED,
            "state": _COPIED,
            **prov,
        },
        written(site.Homework, {"course"}) | {"cohort"},
        frozenset(
            {
                # C7.11 (v0.5.1) binds a homework to the module and unit whose
                # page shows its form, and fills both from the cohort's homework
                # manifest during the course sync.  The DataTalks.Club site has
                # no such binding to copy, so this one-time import leaves the
                # two nullable keys at their default and the sync sets them.
                "module",
                "unit",
            }
        ),
    )

    mapping[("courses.Question", "cb_coursework.Question", False)] = (
        {
            "homework": _COPIED,
            "text": _COPIED,
            "question_type": _COPIED,
            "answer_type": _COPIED,
            "possible_answers": _COPIED,
            "correct_answer": _COPIED,
            "source_question_id": _COPIED,
            "source_option_ids": _COPIED,
            "answer_envelope": _COPIED,
            "scores_for_correct_answer": _COPIED,
            **prov,
        },
        written(site.Question, set()),
        frozenset(),
    )

    mapping[("courses.Submission", "cb_coursework.Submission", False)] = (
        {
            "homework": _COPIED,
            "student": _COPIED,
            "enrollment": _COPIED,
            "homework_link": _COPIED,
            "learning_in_public_links": _COPIED,
            "time_spent_lectures": _COPIED,
            "time_spent_homework": _COPIED,
            "problems_comments": _COPIED,
            "faq_contribution": _COPIED,
            "faq_contribution_url": _COPIED,
            "submitted_at": _COPIED,
            "questions_score": _COPIED,
            "faq_score": _COPIED,
            "learning_in_public_score": _COPIED,
            "total_score": _COPIED,
        },
        written(site.Submission, set()),
        frozenset(),
    )

    mapping[("courses.Answer", "cb_coursework.Answer", False)] = (
        {
            "submission": _COPIED,
            "question": _COPIED,
            "answer_text": _COPIED,
            "is_correct": _COPIED,
        },
        written(site.Answer, set()),
        frozenset(),
    )

    mapping[("courses.HomeworkStatistics", "cb_coursework.HomeworkStatistics", False)] = (
        {
            "homework": _COPIED,
            "last_calculated": "copied verbatim (decision 13)",
            **concrete(site.HomeworkStatistics, {"id", "homework", "last_calculated"}),
        },
        written(site.HomeworkStatistics, set()),
        frozenset(),
    )

    mapping[("courses.Project", "cb_coursework.Project", False)] = (
        {
            "course": "copied as cohort",
            "slug": _COPIED,
            "title": _COPIED,
            "description": _COPIED,
            "instructions_url": _COPIED,
            "submission_due_date": _COPIED,
            "learning_in_public_cap_project": _COPIED,
            "peer_review_due_date": _COPIED,
            "time_spent_project_field": _COPIED,
            "problems_comments_field": _COPIED,
            "faq_contribution_field": _COPIED,
            "learning_in_public_cap_review": _COPIED,
            "number_of_peers_to_evaluate": _COPIED,
            "points_for_peer_review": _COPIED,
            "time_spent_evaluation_field": _COPIED,
            "state": _COPIED,
        },
        written(site.Project, {"course"}) | {"cohort"},
        # Decision 18: pooled review is a package-only capability (C5.2g).  The site has no
        # review window to copy, and deriving one from ``peer_review_due_date`` would fabricate
        # an operator knob out of a historical date, so the package default (7 days) stands and
        # an operator sets it when a pooled project is first run.
        frozenset({"pooled_review_window_days"}),
    )

    mapping[("courses.ReviewCriteria", "cb_coursework.ReviewCriteria", False)] = (
        {
            "course": "copied as cohort",
            "description": _COPIED,
            "options": _COPIED,
            "review_criteria_type": _COPIED,
        },
        written(site.ReviewCriteria, {"course"}) | {"cohort"},
        frozenset(),
    )

    mapping[
        ("courses.ProjectCriteriaAssignment", "cb_coursework.ProjectCriteriaAssignment", False)
    ] = (
        {
            "project": _COPIED,
            "criteria": _COPIED,
            "position": _COPIED,
        },
        written(site.ProjectCriteriaAssignment, set()),
        frozenset(),
    )

    mapping[("courses.ProjectSubmission", "cb_coursework.ProjectSubmission", False)] = (
        {
            "project": "copied; its state also derives review_state (decision 18)",
            "student": _COPIED,
            "enrollment": _COPIED,
            "github_link": _COPIED,
            "commit_id": _COPIED,
            "learning_in_public_links": _COPIED,
            "faq_contribution": _COPIED,
            "faq_contribution_url": _COPIED,
            "time_spent": _COPIED,
            "problems_comments": _COPIED,
            "submitted_at": _COPIED,
            "project_score": _COPIED,
            "project_faq_score": _COPIED,
            "project_learning_in_public_score": _COPIED,
            "peer_review_score": _COPIED,
            "peer_review_learning_in_public_score": _COPIED,
            "total_score": _COPIED,
            "reviewed_enough_peers": _COPIED,
            "passed": _COPIED,
            "volunteer_review_only": _COPIED,
        },
        written(site.ProjectSubmission, set()) | {"review_state"},
        frozenset(),
    )

    mapping[("courses.ProjectVote", "cb_coursework.ProjectVote", False)] = (
        {
            "submission": _COPIED,
            "voter": _COPIED,
            "created_at": "copied verbatim (decision 13)",
        },
        written(site.ProjectVote, set()),
        frozenset(),
    )

    mapping[("courses.PeerReview", "cb_coursework.PeerReview", False)] = (
        {
            "submission_under_evaluation": _COPIED,
            "reviewer": _COPIED,
            "note_to_peer": _COPIED,
            "learning_in_public_links": _COPIED,
            "time_spent_reviewing": _COPIED,
            "problems_comments": _COPIED,
            "optional": _COPIED,
            "submitted_at": _COPIED,
            "state": _COPIED,
        },
        written(site.PeerReview, set()),
        # Decision 18: a pooled review batch (C5.2f) is a package-only row.  Every migrated
        # review is deadline-mode, whose due date is its project's ``peer_review_due_date``,
        # and for which the package's own contract is ``batch = None``.  The import creates no
        # ``PeerReviewBatch`` rows and leaves the FK null.
        frozenset({"batch"}),
    )

    mapping[("courses.CriteriaResponse", "cb_coursework.CriteriaResponse", False)] = (
        {
            "review": _COPIED,
            "criteria": _COPIED,
            "answer": _COPIED,
        },
        written(site.CriteriaResponse, set()),
        frozenset(),
    )

    mapping[("courses.ProjectEvaluationScore", "cb_coursework.ProjectEvaluationScore", False)] = (
        {
            "submission": _COPIED,
            "review_criteria": _COPIED,
            "score": _COPIED,
        },
        written(site.ProjectEvaluationScore, set()),
        frozenset(),
    )

    mapping[("courses.ProjectStatistics", "cb_coursework.ProjectStatistics", False)] = (
        {
            "project": _COPIED,
            "last_calculated": "copied verbatim (decision 13)",
            **concrete(site.ProjectStatistics, {"id", "project", "last_calculated"}),
        },
        written(site.ProjectStatistics, set()),
        frozenset(),
    )

    mapping[("courses.LeaderboardComplaint", "cb_coursework.LeaderboardComplaint", False)] = (
        {
            "enrollment": _COPIED,
            "reporter": _COPIED,
            "issue_type": _COPIED,
            "description": _COPIED,
            "resolved": _COPIED,
            "resolved_at": _COPIED,
            "resolved_by": _COPIED,
            "created_at": "copied verbatim (decision 13)",
        },
        written(site.LeaderboardComplaint, set()),
        frozenset(),
    )

    mapping[("courses.RegistrationCampaign", "cb_coursework.RegistrationCampaign", False)] = (
        {
            "slug": _COPIED,
            "title": _COPIED,
            "edition_label": _COPIED,
            "current_course": "copied as current_cohort",
            "is_active": _COPIED,
            "registration_baseline_cohort": _COPIED,
            "registration_baseline_count": _COPIED,
            "registration_native_start_at": _COPIED,
            "marketing_markdown": _COPIED,
            "meta_description": _COPIED,
            "hero_image_url": _COPIED,
            "video_url": _COPIED,
            "created_at": "copied verbatim (decision 13)",
            "updated_at": "copied verbatim (decision 13)",
        },
        written(site.RegistrationCampaign, {"current_course"}) | {"current_cohort"},
        frozenset(),
    )

    mapping[("courses.CourseRegistration", "cb_coursework.CourseRegistration", False)] = (
        {
            "campaign": _COPIED,
            "course": "copied as cohort; a site null is preserved over the save() default",
            "user": _COPIED,
            "email": _COPIED,
            "email_normalized": _COPIED,
            "name": _COPIED,
            "company_name": _COPIED,
            "country": _COPIED,
            "region": _COPIED,
            "role": _COPIED,
            "comment": _COPIED,
            "accepted_newsletter": _COPIED,
            "created_at": "copied verbatim (decision 13)",
            "updated_at": "copied verbatim (decision 13)",
        },
        written(site.CourseRegistration, {"course"}) | {"cohort"},
        frozenset(),
    )

    mapping[("courses.Testimonial", "cb_coursework.Testimonial", False)] = (
        {
            "placement": _COPIED,
            "course": _COPIED,
            "name": _COPIED,
            "attribution": _COPIED,
            "quote": _COPIED,
            "source_url": _COPIED,
            "portrait_asset_key": _COPIED,
            "role_before": _COPIED,
            "role_after": _COPIED,
            "elapsed": _COPIED,
            "position": _COPIED,
            "published": _COPIED,
        },
        written(site.Testimonial, set()),
        frozenset(),
    )

    mapping[("courses.WrappedStatistics", "cb_coursework.WrappedStatistics", False)] = (
        {
            "year": _COPIED,
            "is_visible": _COPIED,
            "calculated_at": "copied verbatim (decision 13)",
            "created_at": "copied verbatim (decision 13)",
            **concrete(site.WrappedStatistics, {"id", "calculated_at", "created_at"}),
        },
        written(site.WrappedStatistics, set()),
        frozenset(),
    )

    mapping[("courses.UserWrappedStatistics", "cb_coursework.UserWrappedStatistics", False)] = (
        {
            "wrapped": _COPIED,
            "user": _COPIED,
            "calculated_at": "copied verbatim (decision 13)",
            **concrete(
                site.UserWrappedStatistics,
                {"id", "wrapped", "user", "calculated_at"},
            ),
        },
        written(site.UserWrappedStatistics, set()),
        frozenset(),
    )

    # Site-only pairs: rows that do not migrate, named so they still cannot
    # drift.  The package side is checked by the pair that writes the model.
    site_only_stays = {
        "courses.SharedCurriculum": (
            "course",
            "parser_version",
            "updated_at",
            "source_content_id",
            "source_path",
            "source_commit_sha",
            "source_checksum",
        ),
        "courses.SharedCurriculumAsset": (
            "lesson",
            "public_path",
            "storage_key",
            "content_type",
            "byte_size",
            "source_content_id",
            "source_path",
            "source_commit_sha",
            "source_checksum",
        ),
        "courses.CurriculumRouteAlias": (
            "old_path",
            "target_kind",
            "shared_module",
            "shared_lesson",
            "archive_cohort",
            "archive_path",
            "reason",
            "source_commit_sha",
            "active",
        ),
        "courses.Module": (
            "cohort",
            "position",
            "slug",
            "title",
            "link",
            "terminal_homework",
            "source_content_id",
            "source_path",
            "source_commit_sha",
            "source_checksum",
        ),
        "courses.Unit": (
            "module",
            "position",
            "slug",
            "title",
            "content_markdown",
            "rendered_html",
            "link",
            "video_url",
            "code_sources",
            "source_content_id",
            "source_path",
            "source_commit_sha",
            "source_checksum",
        ),
        "courses.UnitReadState": ("user", "unit", "read_at"),
        "courses.CurriculumFlowItem": ("cohort", "position", "module", "project"),
    }
    for model_name, field_names in site_only_stays.items():
        mapping[(model_name, model_name, True)] = (
            {name: _STAYS for name in field_names},
            frozenset(),
            frozenset(),
        )

    return mapping


def import_course_platform(*, apply: bool = False) -> dict[str, Any]:
    """Copy the site course platform into the shared tables and verify counts.

    The whole copy is one transaction; a dry run rolls it back and returns the
    report it would have produced.  Refusals (decision 12, the delivery-mode
    guard, the mapping-coverage check, the self-paced uniqueness, the count
    verification) happen before any family is written or roll the transaction
    back, so a refused run leaves no partial copy.
    """

    from django.db import transaction

    from courses import models as site

    _refuse_mapping_drift()
    _refuse_without_shared_graph()
    _refuse_unknown_delivery_modes()
    _refuse_ambiguous_self_paced()

    with transaction.atomic():
        report = _Report()
        maps = _Maps()

        _import_courses(site, maps, report)
        _import_cohorts(site, maps, report)
        _import_shared_modules(site, maps, report)
        _import_shared_lessons(site, maps, report)
        _import_placements(site, maps, report)
        _import_enrollments(site, maps, report)
        _import_progress(site, maps, report)
        _import_homework(site, maps, report)
        _import_questions(site, maps, report)
        _import_submissions(site, maps, report)
        _import_answers(site, maps, report)
        _import_homework_statistics(site, maps, report)
        _import_review_criteria(site, maps, report)
        _import_projects(site, maps, report)
        _import_criteria_assignments(site, maps, report)
        _import_project_submissions(site, maps, report)
        _import_project_votes(site, maps, report)
        _import_peer_reviews(site, maps, report)
        _import_criteria_responses(site, maps, report)
        _import_evaluation_scores(site, maps, report)
        _import_project_statistics(site, maps, report)
        _import_complaints(site, maps, report)
        _import_campaigns(site, maps, report)
        _import_course_registrations(site, maps, report)
        _import_testimonials(site, maps, report)
        _import_wrapped(site, maps, report)
        _count_leftovers(site, report)

        verification = _verify_counts(site, report)
        if not apply:
            transaction.set_rollback(True)

    result = {"applied": apply, "verification": verification, **report.as_dict()}
    return result


class _Maps:
    """Site pk -> shared row, one per family, valid inside a single run."""

    def __init__(self) -> None:
        self.courses: dict[Any, Any] = {}
        self.cohorts: dict[Any, Any] = {}
        self.modules: dict[Any, Any] = {}
        self.units: dict[Any, Any] = {}
        self.enrollments: dict[Any, Any] = {}
        self.homework: dict[Any, Any] = {}
        self.questions: dict[Any, Any] = {}
        self.submissions: dict[Any, Any] = {}
        self.criteria: dict[Any, Any] = {}
        self.projects: dict[Any, Any] = {}
        self.project_submissions: dict[Any, Any] = {}
        self.peer_reviews: dict[Any, Any] = {}
        self.campaigns: dict[Any, Any] = {}


# --------------------------------------------------------------------------
# Curriculum: courses, cohorts, shared graph
# --------------------------------------------------------------------------


def _import_courses(site: Any, maps: _Maps, report: _Report) -> None:
    from community_base.curriculum.models import Course

    families = list(site.Course.objects.all().order_by("pk"))
    report.family("courses", len(families))
    for family in families:
        values = {
            "title": family.title,
            "description": family.description,
            "github_repo_url": family.github_repo_url or "",
            "docs_url": family.docs_url or "",
            "faq_url": family.faq_document_url or "",
            "hashtag": family.social_media_hashtag or "",
            "visible": family.visible,
            # Decision 10: the site visible flag decides the catalogue status.
            "status": (_COURSE_STATUS_VISIBLE if family.visible else _COURSE_STATUS_HIDDEN),
            # Decision 5: starting_point/prerequisites/weekly_commitment/
            # progression/outcome stay site-side. Decisions 6/7/8: the
            # testimonial JSON column, course people and provenance id shape
            # are handled below or not at all.
            **_package_provenance(family),
        }
        course, _ = Course.objects.update_or_create(slug=family.slug, defaults=values)
        maps.courses[family.pk] = course
        report.migrated("courses")
        if family.source_content_id is None:
            report.gap("courses:provenance_absent")


def _import_cohorts(site: Any, maps: _Maps, report: _Report) -> None:
    from community_base.curriculum.models import Cohort

    cohorts = list(site.Cohort.objects.select_related("course").order_by("pk"))
    report.family("cohorts", len(cohorts))
    for cohort in cohorts:
        values = {
            "title": cohort.title,
            # Decision 9: the rename lives in this mapping.
            "mode": _MODE_LIVE_TO_COHORT[cohort.delivery_mode],
            "start_date": cohort.start_date,
            "end_date": cohort.end_date,
            "registration_url": cohort.registration_url or "",
            "hashtag": cohort.social_media_hashtag or "",
            "finished": cohort.finished,
            "visible": cohort.visible,
            "max_participants": None,
            "project_passing_score": cohort.project_passing_score,
            "min_projects_to_pass": cohort.min_projects_to_pass,
            "first_homework_scored": cohort.first_homework_scored,
            "homework_problems_comments_field": cohort.homework_problems_comments_field,
            # Decision 5: cohort-level description/outcome/promo_summary/
            # delivery_format/github_repo_url and the archive provenance and
            # year stay site-side; the legacy edition slug stays too (the
            # redirect duty survives the compatibility window).
            **_package_provenance(cohort),
        }
        shared, _ = Cohort.objects.update_or_create(
            course=maps.courses[cohort.course_id],
            # The identifier is the public route identity (mapping: Cohort
            # field deltas); the legacy edition slug is only a fallback for
            # a row that somehow carries no identifier at all.
            slug=cohort.identifier or cohort.slug,
            defaults=values,
        )
        maps.cohorts[cohort.pk] = shared
        report.migrated("cohorts")
        if cohort.source_content_id is None:
            report.gap("cohorts:provenance_absent")


def _import_shared_modules(site: Any, maps: _Maps, report: _Report) -> None:
    from community_base.curriculum.models import Module

    modules = list(
        site.SharedModule.objects.select_related("curriculum__course").order_by(
            "curriculum_id", "position", "pk"
        )
    )
    report.family("shared_modules", len(modules))
    for module in modules:
        if not module.published:
            # Decision 11: unpublished rows are skipped and counted.
            report.skipped("shared_modules", "unpublished", module.slug)
            continue
        course = maps.courses[module.curriculum.course_id]
        provenance = _package_provenance(module)
        values = {
            "title": module.title,
            "sort_order": module.position,
            "overview": module.overview_markdown,
            **provenance,
        }
        shared = None
        if provenance["source_content_id"] is not None:
            shared = Module.objects.filter(
                course=course, source_content_id=provenance["source_content_id"]
            ).first()
        if shared is None:
            shared, _ = Module.objects.update_or_create(
                course=course, parent=None, slug=module.slug, defaults=values
            )
        else:
            values["slug"] = module.slug
            for field, value in values.items():
                setattr(shared, field, value)
            shared.save()
        # Byte-identical: the site's rendered overview HTML survives the
        # package's own markdown rendering untouched.
        Module.objects.filter(pk=shared.pk).update(overview_html=module.overview_rendered_html)
        maps.modules[module.pk] = shared
        report.migrated("shared_modules")
        if module.source_content_id is None:
            report.gap("shared_modules:provenance_absent")


def _import_shared_lessons(site: Any, maps: _Maps, report: _Report) -> None:
    from community_base.curriculum.models import Unit

    lessons = list(
        site.SharedLesson.objects.select_related("module").order_by("module_id", "position", "pk")
    )
    report.family("shared_lessons", len(lessons))
    for lesson in lessons:
        shared_module = maps.modules.get(lesson.module_id)
        if shared_module is None:
            # Its module was skipped (decision 11), so the lesson cannot land.
            reason = "retired" if lesson.retired_at else "unpublished"
            report.skipped("shared_lessons", f"module_skipped_{reason}", lesson.slug)
            continue
        if not lesson.published or lesson.retired_at:
            report.skipped("shared_lessons", "unpublished_or_retired", lesson.slug)
            continue
        if lesson.code_sources:
            # Decision 4: the code-source content has no package column; the
            # lesson itself still migrates.
            report.gap("lesson_code_sources")
        provenance = _package_provenance(lesson)
        values = {
            "title": lesson.title,
            "sort_order": lesson.position,
            "video_url": lesson.video_url or "",
            "body": lesson.content_markdown or "",
            **provenance,
        }
        unit = None
        if provenance["source_content_id"] is not None:
            unit = Unit.objects.filter(
                module=shared_module, source_content_id=provenance["source_content_id"]
            ).first()
        if unit is None:
            unit, _ = Unit.objects.update_or_create(
                module=shared_module, slug=lesson.slug, defaults=values
            )
        else:
            values["slug"] = lesson.slug
            for field, value in values.items():
                setattr(unit, field, value)
            unit.save()
        # Byte-identical: the site's rendered HTML survives the package's own
        # markdown rendering untouched.
        Unit.objects.filter(pk=unit.pk).update(body_html=lesson.rendered_html)
        maps.units[lesson.pk] = unit
        report.migrated("shared_lessons")
        if lesson.source_content_id is None:
            report.gap("shared_lessons:provenance_absent")


def _import_placements(site: Any, maps: _Maps, report: _Report) -> None:
    from community_base.curriculum.models import CohortModule

    placements = list(
        site.CohortSharedModule.objects.select_related("cohort", "shared_module").order_by(
            "cohort_id", "position", "pk"
        )
    )
    report.family("placements", len(placements))
    for placement in placements:
        if placement.terminal_homework_id:
            # Decision 2: the binding has no package column; counted, and the
            # placement itself still migrates.
            report.gap("terminal_homework_bindings")
        module = maps.modules.get(placement.shared_module_id)
        cohort = maps.cohorts[placement.cohort_id]
        if module is None:
            report.skipped("placements", "module_skipped")
            continue
        CohortModule.objects.update_or_create(
            cohort=cohort, module=module, defaults={"sort_order": placement.position}
        )
        report.migrated("placements")


def _import_enrollments(site: Any, maps: _Maps, report: _Report) -> None:
    from community_base.curriculum.models import Certificate, Enrollment

    enrollments = list(site.Enrollment.objects.select_related("course", "student").order_by("pk"))
    report.family("enrollments", len(enrollments))
    with_certificate = sum(1 for row in enrollments if row.certificate_url)
    report.family("certificates", with_certificate)
    for enrollment in enrollments:
        values = {
            "unenrolled_at": None,
            "source": "manual",
            "display_name": enrollment.display_name or "",
            "display_on_leaderboard": enrollment.display_on_leaderboard,
            "display_public_profile": enrollment.display_public_profile,
            "position_on_leaderboard": enrollment.position_on_leaderboard,
            "disable_learning_in_public": enrollment.disable_learning_in_public,
            "certificate_name": enrollment.certificate_name or "",
            "total_score": enrollment.total_score,
            "certificate_url": enrollment.certificate_url or "",
        }
        shared, _ = Enrollment.objects.update_or_create(
            user=enrollment.student,
            cohort=maps.cohorts[enrollment.course_id],
            unenrolled_at=None,
            defaults=values,
        )
        maps.enrollments[enrollment.pk] = shared
        report.migrated("enrollments")
        # ``enrolled_at`` is auto_now_add on the shared model; restore the
        # learner's real date after the write instead of letting the copy
        # stamp everyone with import time.
        Enrollment.objects.filter(pk=shared.pk).update(enrolled_at=enrollment.enrollment_date)
        if enrollment.certificate_url:
            # Promote to a Certificate row, url byte-for-byte (issue step 2):
            # the site value passes through untouched, and the checkpoint
            # compares the strings, not a render.
            Certificate.objects.update_or_create(
                enrollment=shared,
                defaults={"url": enrollment.certificate_url},
            )
            report.migrated("certificates")


def _import_progress(site: Any, maps: _Maps, report: _Report) -> None:
    from community_base.curriculum.models import UnitProgress

    states = list(
        site.SharedLessonReadState.objects.select_related("shared_lesson", "user").order_by("pk")
    )
    report.family("progress", len(states))
    for state in states:
        unit = maps.units.get(state.shared_lesson_id)
        if unit is None:
            report.skipped("progress", "lesson_skipped")
            continue
        UnitProgress.objects.update_or_create(
            user=state.user,
            unit=unit,
            defaults={"completed_at": state.read_at},
        )
        report.migrated("progress")


# --------------------------------------------------------------------------
# Coursework
# --------------------------------------------------------------------------


def _import_homework(site: Any, maps: _Maps, report: _Report) -> None:
    from community_base.coursework.models import Homework

    rows = list(site.Homework.objects.select_related("course").order_by("pk"))
    report.family("homework", len(rows))
    for row in rows:
        values = {
            "title": row.title,
            "description": row.description or "",
            "instructions_markdown": row.instructions_markdown or "",
            "instructions_source_path": row.instructions_source_path or "",
            "instructions_url": row.instructions_url,
            "due_date": row.due_date,
            "learning_in_public_cap": row.learning_in_public_cap,
            "homework_url_field": row.homework_url_field,
            "time_spent_lectures_field": row.time_spent_lectures_field,
            "time_spent_homework_field": row.time_spent_homework_field,
            "faq_contribution_field": row.faq_contribution_field,
            "state": row.state,
            **_package_provenance(row),
        }
        shared, _ = Homework.objects.update_or_create(
            cohort=maps.cohorts[row.course_id], slug=row.slug, defaults=values
        )
        maps.homework[row.pk] = shared
        report.migrated("homework")
        if row.source_content_id is None:
            report.gap("homework:provenance_absent")


def _import_questions(site: Any, maps: _Maps, report: _Report) -> None:
    from community_base.coursework.models import Question

    rows = list(site.Question.objects.select_related("homework").order_by("pk"))
    report.family("questions", len(rows))
    for row in rows:
        homework = maps.homework[row.homework_id]
        provenance = _package_provenance(row)
        values = {
            "text": row.text,
            "question_type": row.question_type,
            "answer_type": row.answer_type,
            "possible_answers": row.possible_answers,
            "correct_answer": row.correct_answer,
            "source_question_id": row.source_question_id,
            "source_option_ids": row.source_option_ids,
            "answer_envelope": row.answer_envelope,
            "scores_for_correct_answer": row.scores_for_correct_answer,
            **provenance,
        }
        shared = None
        if provenance["source_content_id"] is not None:
            shared = Question.objects.filter(
                homework=homework, source_content_id=provenance["source_content_id"]
            ).first()
        elif row.source_question_id:
            shared = Question.objects.filter(
                homework=homework, source_question_id=row.source_question_id
            ).first()
        if shared is None:
            # A database-managed question has no stable identity to reconcile
            # on; text and type are the best key and duplicates are caught by
            # the count verification below.
            shared, _ = Question.objects.update_or_create(
                homework=homework,
                text=row.text,
                question_type=row.question_type,
                defaults=values,
            )
        else:
            for field, value in values.items():
                setattr(shared, field, value)
            shared.save()
        maps.questions[row.pk] = shared
        report.migrated("questions")
        if row.source_content_id is None:
            report.gap("questions:provenance_absent")


def _import_submissions(site: Any, maps: _Maps, report: _Report) -> None:
    from community_base.coursework.models import Submission

    rows = list(site.Submission.objects.select_related("homework", "student").order_by("pk"))
    report.family("submissions", len(rows))
    for row in rows:
        values = {
            "enrollment": maps.enrollments[row.enrollment_id],
            "homework_link": row.homework_link,
            "learning_in_public_links": row.learning_in_public_links,
            "time_spent_lectures": row.time_spent_lectures,
            "time_spent_homework": row.time_spent_homework,
            "problems_comments": row.problems_comments or "",
            "faq_contribution": row.faq_contribution or "",
            "faq_contribution_url": row.faq_contribution_url,
            "submitted_at": row.submitted_at,
            "questions_score": row.questions_score,
            "faq_score": row.faq_score,
            "learning_in_public_score": row.learning_in_public_score,
            "total_score": row.total_score,
        }
        shared, _ = Submission.objects.update_or_create(
            homework=maps.homework[row.homework_id],
            student=row.student,
            submitted_at=row.submitted_at,
            defaults=values,
        )
        maps.submissions[row.pk] = shared
        report.migrated("submissions")


def _import_answers(site: Any, maps: _Maps, report: _Report) -> None:
    from community_base.coursework.models import Answer

    rows = list(site.Answer.objects.select_related("submission", "question").order_by("pk"))
    report.family("answers", len(rows))
    for row in rows:
        Answer.objects.update_or_create(
            submission=maps.submissions[row.submission_id],
            question=maps.questions[row.question_id],
            defaults={"answer_text": row.answer_text, "is_correct": row.is_correct},
        )
        report.migrated("answers")


def _import_homework_statistics(site: Any, maps: _Maps, report: _Report) -> None:
    from community_base.coursework.models import HomeworkStatistics

    rows = list(site.HomeworkStatistics.objects.select_related("homework").order_by("pk"))
    report.family("homework_statistics", len(rows))
    stat_fields = [
        field.name
        for field in site.HomeworkStatistics._meta.get_fields()
        if getattr(field, "concrete", False)
        and not field.primary_key
        and field.name not in {"homework", "last_calculated"}
    ]
    for row in rows:
        values = {name: getattr(row, name) for name in stat_fields}
        shared, _ = HomeworkStatistics.objects.update_or_create(
            homework=maps.homework[row.homework_id], defaults=values
        )
        # Decision 13: derived rows are copied, not recomputed -- including
        # when they were last calculated.
        HomeworkStatistics.objects.filter(pk=shared.pk).update(last_calculated=row.last_calculated)
        report.migrated("homework_statistics")


def _import_review_criteria(site: Any, maps: _Maps, report: _Report) -> None:
    from community_base.coursework.models import ReviewCriteria

    rows = list(site.ReviewCriteria.objects.select_related("course").order_by("pk"))
    report.family("review_criteria", len(rows))
    for row in rows:
        values = {
            "cohort": maps.cohorts.get(row.course_id),
            "description": row.description,
            "options": row.options,
            "review_criteria_type": row.review_criteria_type,
        }
        # Criteria have no natural key on either side; reconcile on the full
        # row value so a replay matches what it wrote before, and let the
        # count verification refuse genuine duplicates.
        shared = ReviewCriteria.objects.filter(
            cohort=values["cohort"],
            description=row.description,
            review_criteria_type=row.review_criteria_type,
        ).first()
        if shared is not None and list(shared.options) != list(row.options):
            shared = None
        if shared is None:
            shared = ReviewCriteria.objects.create(**values)
        else:
            for field, value in values.items():
                setattr(shared, field, value)
            shared.save()
        maps.criteria[row.pk] = shared
        report.migrated("review_criteria")


def _import_projects(site: Any, maps: _Maps, report: _Report) -> None:
    from community_base.coursework.models import Project

    rows = list(site.Project.objects.select_related("course").order_by("pk"))
    report.family("projects", len(rows))
    for row in rows:
        values = {
            "title": row.title,
            "description": row.description or "",
            "instructions_url": row.instructions_url,
            "submission_due_date": row.submission_due_date,
            "learning_in_public_cap_project": row.learning_in_public_cap_project,
            "peer_review_due_date": row.peer_review_due_date,
            "time_spent_project_field": row.time_spent_project_field,
            "problems_comments_field": row.problems_comments_field,
            "faq_contribution_field": row.faq_contribution_field,
            "learning_in_public_cap_review": row.learning_in_public_cap_review,
            "number_of_peers_to_evaluate": row.number_of_peers_to_evaluate,
            "points_for_peer_review": row.points_for_peer_review,
            "time_spent_evaluation_field": row.time_spent_evaluation_field,
            "state": row.state,
        }
        shared, _ = Project.objects.update_or_create(
            cohort=maps.cohorts[row.course_id], slug=row.slug, defaults=values
        )
        maps.projects[row.pk] = shared
        report.migrated("projects")


def _import_criteria_assignments(site: Any, maps: _Maps, report: _Report) -> None:
    from community_base.coursework.models import ProjectCriteriaAssignment

    rows = list(
        site.ProjectCriteriaAssignment.objects.select_related("project", "criteria").order_by("pk")
    )
    report.family("criteria_assignments", len(rows))
    for row in rows:
        ProjectCriteriaAssignment.objects.update_or_create(
            project=maps.projects[row.project_id],
            criteria=maps.criteria[row.criteria_id],
            defaults={"position": row.position},
        )
        report.migrated("criteria_assignments")


def _import_project_submissions(site: Any, maps: _Maps, report: _Report) -> None:
    from community_base.coursework.models import ProjectSubmission

    rows = list(site.ProjectSubmission.objects.select_related("project", "student").order_by("pk"))
    report.family("project_submissions", len(rows))
    for row in rows:
        values = {
            "enrollment": maps.enrollments[row.enrollment_id],
            "github_link": row.github_link,
            "commit_id": row.commit_id,
            "learning_in_public_links": row.learning_in_public_links,
            "faq_contribution": row.faq_contribution or "",
            "faq_contribution_url": row.faq_contribution_url,
            "time_spent": row.time_spent,
            "problems_comments": row.problems_comments or "",
            "submitted_at": row.submitted_at,
            "project_score": row.project_score,
            "project_faq_score": row.project_faq_score,
            "project_learning_in_public_score": row.project_learning_in_public_score,
            "peer_review_score": row.peer_review_score,
            "peer_review_learning_in_public_score": row.peer_review_learning_in_public_score,
            "total_score": row.total_score,
            "reviewed_enough_peers": row.reviewed_enough_peers,
            "passed": row.passed,
            "volunteer_review_only": row.volunteer_review_only,
        }
        values["review_state"] = _PROJECT_STATE_TO_REVIEW_STATE.get(
            row.project.state, _REVIEW_STATE_AWAITING_ASSIGNMENT
        )
        shared, _ = ProjectSubmission.objects.update_or_create(
            project=maps.projects[row.project_id],
            student=row.student,
            submitted_at=row.submitted_at,
            defaults=values,
        )
        maps.project_submissions[row.pk] = shared
        report.migrated("project_submissions")


def _import_project_votes(site: Any, maps: _Maps, report: _Report) -> None:
    from community_base.coursework.models import ProjectVote

    rows = list(site.ProjectVote.objects.select_related("submission", "voter").order_by("pk"))
    report.family("project_votes", len(rows))
    for row in rows:
        shared, _ = ProjectVote.objects.update_or_create(
            submission=maps.project_submissions[row.submission_id],
            voter=row.voter,
            defaults={},
        )
        ProjectVote.objects.filter(pk=shared.pk).update(created_at=row.created_at)
        report.migrated("project_votes")


def _import_peer_reviews(site: Any, maps: _Maps, report: _Report) -> None:
    from community_base.coursework.models import PeerReview

    rows = list(
        site.PeerReview.objects.select_related("submission_under_evaluation", "reviewer").order_by(
            "pk"
        )
    )
    report.family("peer_reviews", len(rows))
    for row in rows:
        values = {
            "note_to_peer": row.note_to_peer or "",
            "learning_in_public_links": row.learning_in_public_links,
            "time_spent_reviewing": row.time_spent_reviewing,
            "problems_comments": row.problems_comments or "",
            "optional": row.optional,
            "submitted_at": row.submitted_at,
            "state": row.state,
        }
        shared, _ = PeerReview.objects.update_or_create(
            submission_under_evaluation=maps.project_submissions[
                row.submission_under_evaluation_id
            ],
            reviewer=maps.project_submissions[row.reviewer_id],
            defaults=values,
        )
        maps.peer_reviews[row.pk] = shared
        report.migrated("peer_reviews")


def _import_criteria_responses(site: Any, maps: _Maps, report: _Report) -> None:
    from community_base.coursework.models import CriteriaResponse

    rows = list(site.CriteriaResponse.objects.select_related("review", "criteria").order_by("pk"))
    report.family("criteria_responses", len(rows))
    for row in rows:
        CriteriaResponse.objects.update_or_create(
            review=maps.peer_reviews[row.review_id],
            criteria=maps.criteria[row.criteria_id],
            defaults={"answer": row.answer},
        )
        report.migrated("criteria_responses")


def _import_evaluation_scores(site: Any, maps: _Maps, report: _Report) -> None:
    from community_base.coursework.models import ProjectEvaluationScore

    rows = list(
        site.ProjectEvaluationScore.objects.select_related(
            "submission", "review_criteria"
        ).order_by("pk")
    )
    report.family("evaluation_scores", len(rows))
    for row in rows:
        ProjectEvaluationScore.objects.update_or_create(
            submission=maps.project_submissions[row.submission_id],
            review_criteria=maps.criteria[row.review_criteria_id],
            defaults={"score": row.score},
        )
        report.migrated("evaluation_scores")


def _import_project_statistics(site: Any, maps: _Maps, report: _Report) -> None:
    from community_base.coursework.models import ProjectStatistics

    rows = list(site.ProjectStatistics.objects.select_related("project").order_by("pk"))
    report.family("project_statistics", len(rows))
    stat_fields = [
        field.name
        for field in site.ProjectStatistics._meta.get_fields()
        if getattr(field, "concrete", False)
        and not field.primary_key
        and field.name not in {"project", "last_calculated"}
    ]
    for row in rows:
        values = {name: getattr(row, name) for name in stat_fields}
        shared, _ = ProjectStatistics.objects.update_or_create(
            project=maps.projects[row.project_id], defaults=values
        )
        ProjectStatistics.objects.filter(pk=shared.pk).update(last_calculated=row.last_calculated)
        report.migrated("project_statistics")


def _import_complaints(site: Any, maps: _Maps, report: _Report) -> None:
    from community_base.coursework.models import LeaderboardComplaint

    rows = list(site.LeaderboardComplaint.objects.select_related("enrollment").order_by("pk"))
    report.family("complaints", len(rows))
    for row in rows:
        values = {
            "reporter": row.reporter,
            "description": row.description,
            "resolved": row.resolved,
            "resolved_at": row.resolved_at,
            "resolved_by": row.resolved_by,
        }
        shared, _ = LeaderboardComplaint.objects.update_or_create(
            enrollment=maps.enrollments[row.enrollment_id],
            issue_type=row.issue_type,
            defaults=values,
        )
        # ``created_at`` is auto_now_add on the shared model: the lookup key
        # above must never name it (an insert overwrites the value and a
        # replay would never find its own row again), so it is stamped after
        # the write instead.
        LeaderboardComplaint.objects.filter(pk=shared.pk).update(created_at=row.created_at)
        report.migrated("complaints")


def _import_campaigns(site: Any, maps: _Maps, report: _Report) -> None:
    from community_base.coursework.models import RegistrationCampaign

    rows = list(
        site.RegistrationCampaign.objects.select_related(
            "current_course", "registration_baseline_cohort"
        ).order_by("pk")
    )
    report.family("campaigns", len(rows))
    for row in rows:
        values = {
            "title": row.title,
            "edition_label": row.edition_label or "",
            "current_cohort": maps.cohorts.get(row.current_course_id),
            "is_active": row.is_active,
            "registration_baseline_cohort": maps.cohorts.get(row.registration_baseline_cohort_id),
            "registration_baseline_count": row.registration_baseline_count,
            "registration_native_start_at": row.registration_native_start_at,
            "marketing_markdown": row.marketing_markdown or "",
            "meta_description": row.meta_description or "",
            "hero_image_url": row.hero_image_url or "",
            "video_url": row.video_url or "",
        }
        shared, _ = RegistrationCampaign.objects.update_or_create(slug=row.slug, defaults=values)
        maps.campaigns[row.pk] = shared
        RegistrationCampaign.objects.filter(pk=shared.pk).update(
            created_at=row.created_at, updated_at=row.updated_at
        )
        report.migrated("campaigns")


def _import_course_registrations(site: Any, maps: _Maps, report: _Report) -> None:
    from community_base.coursework.models import CourseRegistration

    rows = list(
        site.CourseRegistration.objects.select_related("campaign", "course", "user").order_by("pk")
    )
    report.family("course_registrations", len(rows))
    for row in rows:
        values = {
            "cohort": maps.cohorts.get(row.course_id),
            "user": row.user,
            "email": row.email,
            "name": row.name,
            "company_name": row.company_name or "",
            "country": row.country,
            "region": row.region,
            "role": row.role,
            "comment": row.comment or "",
            "accepted_newsletter": row.accepted_newsletter,
        }
        shared, _ = CourseRegistration.objects.update_or_create(
            campaign=maps.campaigns[row.campaign_id],
            email_normalized=row.email_normalized,
            defaults=values,
        )
        wanted_cohort_id = values["cohort"].pk if values["cohort"] else None
        if shared.cohort_id != wanted_cohort_id:
            # The shared save() defaults a null cohort to the campaign's
            # current cohort; the site row's null must survive verbatim.
            CourseRegistration.objects.filter(pk=shared.pk).update(cohort=values["cohort"])
        CourseRegistration.objects.filter(pk=shared.pk).update(
            created_at=row.created_at, updated_at=row.updated_at
        )
        report.migrated("course_registrations")


def _import_testimonials(site: Any, maps: _Maps, report: _Report) -> None:
    from community_base.coursework.models import Testimonial

    rows = list(site.Testimonial.objects.select_related("course").order_by("pk"))
    report.family("testimonials", len(rows))
    for row in rows:
        values = {
            "attribution": row.attribution or "",
            "quote": row.quote,
            "source_url": row.source_url or "",
            "portrait_asset_key": row.portrait_asset_key or "",
            "role_before": row.role_before or "",
            "role_after": row.role_after or "",
            "elapsed": row.elapsed or "",
            "position": row.position,
            "published": row.published,
        }
        Testimonial.objects.update_or_create(
            placement=row.placement,
            course=maps.courses.get(row.course_id),
            name=row.name,
            quote=row.quote,
            defaults=values,
        )
        report.migrated("testimonials")


def _import_wrapped(site: Any, maps: _Maps, report: _Report) -> None:
    from community_base.coursework.models import UserWrappedStatistics, WrappedStatistics

    rows = list(site.WrappedStatistics.objects.order_by("pk"))
    user_rows_total = site.UserWrappedStatistics.objects.count()
    report.family("wrapped", len(rows))
    report.family("user_wrapped", user_rows_total)
    head_fields = [
        field.name
        for field in site.WrappedStatistics._meta.get_fields()
        if getattr(field, "concrete", False)
        and not field.primary_key
        and field.name not in {"calculated_at", "created_at"}
    ]
    user_fields = [
        field.name
        for field in site.UserWrappedStatistics._meta.get_fields()
        if getattr(field, "concrete", False)
        and not field.primary_key
        and field.name not in {"wrapped", "user", "calculated_at"}
    ]
    for row in rows:
        values = {name: getattr(row, name) for name in head_fields}
        shared, _ = WrappedStatistics.objects.update_or_create(year=row.year, defaults=values)
        WrappedStatistics.objects.filter(pk=shared.pk).update(
            calculated_at=row.calculated_at, created_at=row.created_at
        )
        report.migrated("wrapped")

        for user_row in site.UserWrappedStatistics.objects.filter(wrapped=row):
            user_values = {name: getattr(user_row, name) for name in user_fields}
            shared_user, _ = UserWrappedStatistics.objects.update_or_create(
                wrapped=shared,
                user=user_row.user,
                defaults=user_values,
            )
            UserWrappedStatistics.objects.filter(pk=shared_user.pk).update(
                calculated_at=user_row.calculated_at
            )
            report.migrated("user_wrapped")


def _count_leftovers(site: Any, report: _Report) -> None:
    """Count what the decisions leave behind: a gap count is a decision recorded."""

    from django.db.models import Q

    from courses.models import (
        CohortSharedModule,
        CurriculumFlowItem,
        SharedCurriculumAsset,
        SharedLesson,
        SharedModule,
    )

    # Decision 3: project placements have no package home; module placements
    # of the bespoke graph belong to cohorts the refusal above already named.
    flow_total = CurriculumFlowItem.objects.count()
    report.family("flow_items_leftovers", flow_total)
    project_flow = CurriculumFlowItem.objects.filter(project__isnull=False).count()
    module_flow = CurriculumFlowItem.objects.filter(module__isnull=False).count()
    report.counts["flow_items_leftovers"]["skipped"] = project_flow + module_flow
    if project_flow:
        report.gap("flow_item_project_placements")
        report.gaps["flow_item_project_placements"] = project_flow
    if module_flow:
        report.gap("flow_item_module_placements")
        report.gaps["flow_item_module_placements"] = module_flow

    # Decision 17: the module and lesson summary text has no package column.
    summaries = (
        SharedLesson.objects.filter(~Q(summary="")).count()
        + SharedModule.objects.filter(~Q(summary="")).count()
    )
    if summaries:
        report.gap("module_lesson_summaries")
        report.gaps["module_lesson_summaries"] = summaries

    # Decision 6: shared curriculum assets have no package home.
    assets = SharedCurriculumAsset.objects.count()
    if assets:
        report.gap("shared_curriculum_assets")
        report.gaps["shared_curriculum_assets"] = assets

    # Decision 2, site-wide view: every shared placement binding, even on a
    # placement whose module was skipped.
    bindings = CohortSharedModule.objects.filter(terminal_homework__isnull=False).count()
    if bindings and not report.gaps.get("terminal_homework_bindings"):
        report.gap("terminal_homework_bindings")
        report.gaps["terminal_homework_bindings"] = bindings

    # Decision 5: cohorts carrying at least one override the package has no
    # column for stay site-side; count them.
    overrides = 0
    for cohort in site.Cohort.objects.only(
        "description",
        "outcome",
        "promo_summary",
        "delivery_format",
        "github_repo_url",
    ):
        if (
            cohort.description
            or cohort.outcome
            or cohort.promo_summary
            or cohort.delivery_format
            or cohort.github_repo_url
        ):
            overrides += 1
    if overrides:
        report.gap("cohort_overrides")
        report.gaps["cohort_overrides"] = overrides

    # Decision 8: course people stay assembled site-side; the site has no
    # CourseInstructor rows to count, so the ledger records the decision
    # rather than a number.


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------


def _migratable_site_counts(site: Any) -> dict[str, int]:
    from django.db.models import Q

    lesson_migratable = Q(
        shared_lesson__published=True,
        shared_lesson__retired_at__isnull=True,
        shared_lesson__module__published=True,
    )
    return {
        "courses": site.Course.objects.count(),
        "cohorts": site.Cohort.objects.count(),
        "shared_modules": site.SharedModule.objects.filter(published=True).count(),
        "shared_lessons": site.SharedLesson.objects.filter(
            published=True, retired_at__isnull=True, module__published=True
        ).count(),
        "placements": site.CohortSharedModule.objects.filter(shared_module__published=True).count(),
        "enrollments": site.Enrollment.objects.count(),
        "certificates": site.Enrollment.objects.filter(
            ~Q(certificate_url="") & Q(certificate_url__isnull=False)
        ).count(),
        "progress": site.SharedLessonReadState.objects.filter(lesson_migratable).count(),
        "homework": site.Homework.objects.count(),
        "questions": site.Question.objects.count(),
        "submissions": site.Submission.objects.count(),
        "answers": site.Answer.objects.count(),
        "homework_statistics": site.HomeworkStatistics.objects.count(),
        "review_criteria": site.ReviewCriteria.objects.count(),
        "projects": site.Project.objects.count(),
        "criteria_assignments": site.ProjectCriteriaAssignment.objects.count(),
        "project_submissions": site.ProjectSubmission.objects.count(),
        "project_votes": site.ProjectVote.objects.count(),
        "peer_reviews": site.PeerReview.objects.count(),
        "criteria_responses": site.CriteriaResponse.objects.count(),
        "evaluation_scores": site.ProjectEvaluationScore.objects.count(),
        "project_statistics": site.ProjectStatistics.objects.count(),
        "complaints": site.LeaderboardComplaint.objects.count(),
        "campaigns": site.RegistrationCampaign.objects.count(),
        "course_registrations": site.CourseRegistration.objects.count(),
        "testimonials": site.Testimonial.objects.count(),
        "wrapped": site.WrappedStatistics.objects.count(),
        "user_wrapped": site.UserWrappedStatistics.objects.count(),
    }


def _verify_counts(site: Any, report: _Report) -> dict[str, Any]:
    """The D5.1 verification: per-family site and package counts must be equal."""

    from community_base.coursework import models as cw
    from community_base.curriculum import models as cur

    package_counts = {
        "courses": cur.Course.objects.count(),
        "cohorts": cur.Cohort.objects.count(),
        "shared_modules": cur.Module.objects.count(),
        "shared_lessons": cur.Unit.objects.count(),
        "placements": cur.CohortModule.objects.count(),
        "enrollments": cur.Enrollment.objects.count(),
        "certificates": cur.Certificate.objects.count(),
        "progress": cur.UnitProgress.objects.count(),
        "homework": cw.Homework.objects.count(),
        "questions": cw.Question.objects.count(),
        "submissions": cw.Submission.objects.count(),
        "answers": cw.Answer.objects.count(),
        "homework_statistics": cw.HomeworkStatistics.objects.count(),
        "review_criteria": cw.ReviewCriteria.objects.count(),
        "projects": cw.Project.objects.count(),
        "criteria_assignments": cw.ProjectCriteriaAssignment.objects.count(),
        "project_submissions": cw.ProjectSubmission.objects.count(),
        "project_votes": cw.ProjectVote.objects.count(),
        "peer_reviews": cw.PeerReview.objects.count(),
        "criteria_responses": cw.CriteriaResponse.objects.count(),
        "evaluation_scores": cw.ProjectEvaluationScore.objects.count(),
        "project_statistics": cw.ProjectStatistics.objects.count(),
        "complaints": cw.LeaderboardComplaint.objects.count(),
        "campaigns": cw.RegistrationCampaign.objects.count(),
        "course_registrations": cw.CourseRegistration.objects.count(),
        "testimonials": cw.Testimonial.objects.count(),
        "wrapped": cw.WrappedStatistics.objects.count(),
        "user_wrapped": cw.UserWrappedStatistics.objects.count(),
    }
    site_counts = _migratable_site_counts(site)
    verification = {
        family: {
            "site": site_counts[family],
            "package": package_counts[family],
            "equal": site_counts[family] == package_counts[family],
        }
        for family in site_counts
    }
    mismatched = [family for family, entry in verification.items() if not entry["equal"]]
    if mismatched:
        raise CountMismatch(mismatched)
    return verification


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_target_arguments(parser)
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Write the copy. Without it the run computes everything in a "
            "rolled-back transaction and prints the report it would have "
            "produced -- the counts an operator compares before applying."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        parser = _parser()
        args = parser.parse_args(argv)
        configure_target(parser, args)
        report = import_course_platform(apply=args.apply)
    except CoursePlatformImportError as error:
        payload: dict[str, Any] = {"error": str(error)}
        if error.course_slugs:
            payload["courses"] = list(error.course_slugs)
            payload["instruction"] = (
                "run manage.py migrate_shared_curriculum --course <slug> --apply first"
            )
        if isinstance(error, UnknownDeliveryMode):
            payload["cohorts"] = list(error.named)
            payload["instruction"] = (
                "extend the delivery-mode mapping in the importer deliberately, never as a datafix"
            )
        if isinstance(error, MappingCoverageDrift):
            payload["models"] = list(error.models)
            payload["instruction"] = (
                "name the new field in the mapping registry and decide its "
                "verdict before running the import"
            )
        if isinstance(error, CountMismatch):
            payload["families"] = list(error.families)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
