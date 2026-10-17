"""Bounded operator queries contain no recipient data or transport credentials."""

from django.core.exceptions import ValidationError
from django.db.models import Model

from courses.models import (
    Announcement,
    AnnouncementAssignment,
    AnnouncementTemplate,
    AnnouncementTemplateVersion,
    Cohort,
    CohortAnnouncementPlan,
    Course,
)
from courses.services.launch_announcements import serialize_announcement
from courses.services.launch_assignments import serialize_assignment
from courses.services.launch_templates import serialize_template
from courses.services.launch_timing import TIMING_MODELS, serialize_timing
from courses.services.launch_validation import LaunchError


def serialize_version(row):
    return {
        "id": str(row.pk),
        "template_id": str(row.template_id),
        "number": row.number,
        "channel": row.channel,
        "kind": row.kind,
        "subject": row.subject,
        "body": row.body,
        "offset_minutes": row.offset_minutes,
    }


def serialize_course(row):
    return {"id": str(row.pk), "title": row.title, "slug": row.slug}


def serialize_cohort(row):
    return {
        "id": str(row.pk),
        "course_id": str(row.course_id),
        "title": row.title,
        "identifier": row.identifier,
        "delivery_mode": row.delivery_mode,
        "curriculum_source": row.curriculum_source,
        "revision": row.timing_revision,
    }


RESOURCES = {
    "templates": (AnnouncementTemplate, serialize_template),
    "versions": (AnnouncementTemplateVersion, serialize_version),
    "announcements": (Announcement, serialize_announcement),
    "schedules": (Announcement, serialize_announcement),
    "courses": (Course, serialize_course),
    "cohorts": (Cohort, serialize_cohort),
    "assignments": (AnnouncementAssignment, serialize_assignment),
    "plans": (CohortAnnouncementPlan, serialize_assignment),
}


def resource_selection(query):
    model: type[Model]
    resource = query.get("resource", "announcements")
    if resource == "timings":
        kind = query.get("target_kind", "cohort")
        if kind not in TIMING_MODELS:
            raise LaunchError("Unknown timing owner.")
        model = TIMING_MODELS[kind]

        def serializer(row):
            return serialize_timing(kind, row)
    elif resource in RESOURCES:
        model, serializer = RESOURCES[resource]
    else:
        raise LaunchError("Unknown launch resource.")
    return model, serializer


def filtered_rows(model, query):
    try:
        rows = model.objects.order_by("pk")
        if query.get("id"):
            rows = rows.filter(pk=query["id"])
        if query.get("cohort_id") and model is Announcement:
            rows = rows.filter(cohort_id=query["cohort_id"])
    except (ValueError, ValidationError) as error:
        raise LaunchError("Check the resource identifier.") from error
    if model is Announcement:
        rows = rows.select_related("cohort").prefetch_related("deliveries__email_delivery")
    return rows


def read_launch(query, *, context=None):
    del context
    model, serializer = resource_selection(query)
    rows = filtered_rows(model, query)
    try:
        page = max(1, int(query.get("page", 1)))
    except (TypeError, ValueError) as error:
        raise LaunchError("Page must be an integer.") from error
    try:
        page_rows = list(rows[(page - 1) * 50 : page * 50])
        if model is Announcement:
            from courses.services.launch_status import annotate_audiences

            annotate_audiences(page_rows)
        items = [serializer(row) for row in page_rows]
    except (ValueError, ValidationError) as error:
        raise LaunchError("Check the resource identifier.") from error
    return {
        "items": items,
        "page": page,
        "page_size": 50,
        "total_count": rows.count(),
    }
