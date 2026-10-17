"""Canonical delivery starts. Deadlines never supply or change starts."""

from datetime import UTC, datetime, time

from courses.models import Cohort, CohortSharedModule, Homework, Module, Project
from courses.services.launch_validation import (
    LaunchError,
    aware_datetime,
    iso,
    load_row,
    require_revision,
    text,
)

TIMING_MODELS = {
    "cohort": Cohort,
    "module": Module,
    "shared_module_placement": CohortSharedModule,
    "project": Project,
    "homework": Homework,
}


def timing_owner(kind, identity, *, lock=False):
    if kind not in TIMING_MODELS:
        raise LaunchError("Choose cohort, module, shared_module_placement, project or homework.")
    return load_row(TIMING_MODELS[kind], identity, lock=lock)


def owning_cohort(kind, row):
    if kind == "cohort":
        return row
    if kind in {"module", "shared_module_placement"}:
        return row.cohort
    return row.course


def serialize_timing(kind, row):
    return {
        "id": str(row.pk),
        "kind": kind,
        "starts_at": iso(row.starts_at),
        "revision": row.timing_revision,
        "provenance": row.timing_provenance,
    }


def update_timing(payload):
    kind = payload["target_kind"]
    row = timing_owner(kind, payload["id"], lock=True)
    require_revision(row, payload["expected_revision"], "timing_revision")
    start = aware_datetime(payload["starts_at"])
    validate_start(kind, row, start)
    row.starts_at = start
    row.timing_revision += 1
    row.timing_provenance = text(payload["provenance"], "provenance", limit=200)
    row.save(update_fields=["starts_at", "timing_revision", "timing_provenance"])
    from courses.services.launch_generation import refresh_generated

    refresh_generated(owning_cohort(kind, row))
    return serialize_timing(kind, row)


def validate_start(kind, row, start):
    if start is None:
        return
    deadline = getattr(row, "due_date", None)
    if kind == "project":
        deadline = row.submission_due_date
    if kind in {"module", "shared_module_placement"} and row.terminal_homework_id:
        deadline = row.terminal_homework.due_date
    cohort = owning_cohort(kind, row)
    if deadline is not None and start > deadline:
        raise LaunchError("Start must be on or before the assignment deadline.")
    if cohort.end_date and start > datetime.combine(cohort.end_date, time.max, tzinfo=UTC):
        raise LaunchError("Start must be on or before the cohort end date (UTC).")
