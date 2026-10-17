"""Course defaults and explicit cohort overrides select immutable publications."""

import re

from django.db.models import OuterRef, Subquery

from courses.models import (
    AnnouncementAssignment,
    AnnouncementTemplateVersion,
    Cohort,
    CohortAnnouncementPlan,
    Course,
)
from courses.services.launch_validation import LaunchError, load_row, require_revision, text


def versions_for_ids(values):
    if not isinstance(values, list) or len(values) > 50 or len(set(values)) != len(values):
        raise LaunchError("Choose at most 50 distinct published template versions.")
    versions = list(
        AnnouncementTemplateVersion.objects.filter(pk__in=values, template__archived=False)
    )
    if len(versions) != len(values):
        raise LaunchError("A selected publication is unavailable.")
    return versions


def assignment_for(payload):
    if "cohort_id" in payload:
        if "telegram_destination_key" in payload:
            raise LaunchError("Set the Telegram destination reference on the course defaults.")
        cohort = load_row(Cohort, payload["cohort_id"], lock=True)
        plan, _ = CohortAnnouncementPlan.objects.get_or_create(cohort=cohort)
        plan.override_defaults = payload.get("override_defaults", True)
        if type(plan.override_defaults) is not bool:
            raise LaunchError("Override defaults must be a boolean.")
        return plan, False
    course = load_row(Course, payload["course_id"], lock=True)
    row, _ = AnnouncementAssignment.objects.get_or_create(course=course)
    destination = destination_reference(
        payload.get("telegram_destination_key", row.telegram_destination_key)
    )
    changed = destination != row.telegram_destination_key
    row.telegram_destination_key = destination
    return row, changed


def save_assignment(payload):
    versions = versions_for_ids(payload["version_ids"])
    row, destination_changed = assignment_for(payload)
    require_revision(row, payload["expected_revision"])
    row.versions.set(versions)
    row.revision += 1
    row.save()
    from courses.services.launch_reconcile import reconcile_assignment

    reconcile_assignment(row, destination_changed=destination_changed)
    row.refresh_from_db()
    return serialize_assignment(row)


def serialize_assignment(row):
    result = {
        "id": str(row.pk),
        "revision": row.revision,
        "version_ids": [str(version.pk) for version in row.versions.all()],
    }
    if isinstance(row, AnnouncementAssignment):
        result.update(
            course_id=str(row.course_id), telegram_destination_key=row.telegram_destination_key
        )
    else:
        result.update(
            cohort_id=str(row.cohort_id),
            override_defaults=row.override_defaults,
            launched=row.launched,
        )
    return result


def create_course(payload):
    defaults = [str(row.pk) for row in latest_publications()[:50]]
    versions = versions_for_ids(payload.get("version_ids", defaults))
    course = Course(
        slug=text(payload["slug"], "slug", limit=50),
        title=text(payload["title"], "title", limit=200),
    )
    course.full_clean()
    course.save()
    assignment = AnnouncementAssignment.objects.create(course=course)
    assignment.versions.set(versions)
    return {
        "id": str(course.pk),
        "slug": course.slug,
        "title": course.title,
        "assignment": serialize_assignment(assignment),
    }


def create_cohort(payload):
    course = load_row(Course, payload["course_id"])
    cohort = Cohort(
        course=course,
        slug=payload["slug"],
        identifier=payload["identifier"],
        title=payload["title"],
        description=payload["description"],
        year=payload["year"],
        delivery_mode=payload.get("delivery_mode", "live"),
        curriculum_format=payload.get("curriculum_format", "modules"),
    )
    cohort.full_clean()
    cohort.save()
    CohortAnnouncementPlan.objects.create(cohort=cohort)
    return {"id": str(cohort.pk), "title": cohort.title, "revision": 0}


def latest_publications():
    latest = (
        AnnouncementTemplateVersion.objects.filter(template_id=OuterRef("template_id"))
        .order_by("-number")
        .values("pk")[:1]
    )
    return AnnouncementTemplateVersion.objects.filter(
        pk=Subquery(latest), template__archived=False
    ).order_by("pk")


def destination_reference(value):
    value = text(value, "destination setting key", limit=50, blank=True)
    if value and re.fullmatch(r"[a-z][a-z0-9_-]{0,49}", value) is None:
        raise LaunchError("Use a destination setting key, not a chat ID, URL or credential.")
    return value
