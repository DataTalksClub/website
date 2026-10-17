"""Allowlisted scalar context, resolved from current cohort ownership."""

from courses.services.launch_links import (
    absolute_route,
    assignment_url,
    cohort_kwargs,
    cohort_url,
    module_url,
)
from courses.services.launch_templates import PLACEHOLDERS, render_copy
from courses.services.launch_timing import owning_cohort, timing_owner
from courses.services.launch_validation import LaunchError, iso


def announcement_context(kind, identity, overrides=None):
    row = timing_owner(kind, identity)
    cohort = owning_cohort(kind, row)
    base = cohort_url(cohort)
    context: dict[str, str | bool] = dict.fromkeys(PLACEHOLDERS, "")
    context.update(
        course_title=cohort.course.title,
        cohort_title=cohort.title,
        cohort_year=str(cohort.year),
        cohort_start_date=iso(cohort.starts_at) or "",
        registration_url=cohort.registration_url,
        course_platform_url=base,
        starts_at=iso(row.starts_at) or "",
        leaderboard_url=absolute_route("cohort_leaderboard", **cohort_kwargs(cohort)),
        browser_view_url=base,
    )
    context.update(target_context(kind, row, cohort))
    if overrides is None:
        overrides = {}
    if not isinstance(overrides, dict) or set(overrides) - PLACEHOLDERS:
        raise LaunchError("Context contains unsupported fields.")
    for name, value in overrides.items():
        if not isinstance(value, str | bool) or len(str(value)) > 2000:
            raise LaunchError("Context values must be bounded scalar text or booleans.")
        context[name] = value
    return context


def target_context(kind, row, cohort):
    result: dict[str, str | bool] = {}
    if kind == "project":
        return {"project_title": row.title, "project_url": assignment_url(kind, row, cohort)}
    if kind not in {"module", "shared_module_placement"}:
        return result
    module = row
    if kind == "shared_module_placement":
        module = row.shared_module
    result.update(
        module_number=str(row.position),
        module_title=module.title,
        module_url=module_url(kind, row, cohort),
        homework_available=bool(row.terminal_homework_id),
    )
    if row.terminal_homework_id:
        homework = row.terminal_homework
        result.update(
            homework_number=homework.slug,
            homework_url=assignment_url("homework", homework, cohort),
            homework_submission_url=assignment_url("homework", homework, cohort),
            homework_deadline=iso(homework.due_date) or "",
        )
    return result


def preview(row):
    result = {
        "subject": render_copy(row.subject, row.context),
        "body": render_copy(row.body, row.context),
    }
    if "\n" in result["subject"] or "\r" in result["subject"]:
        raise LaunchError("Email subject must be a single line.")
    if row.channel == "telegram" and len(result["body"]) > 4096:
        raise LaunchError("Telegram messages must fit within 4096 characters.")
    return result


def safe_preview(row):
    try:
        return preview(row)
    except LaunchError as error:
        return {
            "subject": "Preview needs attention",
            "body": "Edit this draft before approval.",
            "error": str(error),
        }
