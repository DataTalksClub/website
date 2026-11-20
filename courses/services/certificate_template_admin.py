"""Staff read and upsert services for per-course certificate templates."""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError

from courses.models.certificate import CertificateTemplate
from courses.models.cohort import Cohort, Course
from courses.models.project import Project

WRITABLE_FIELDS = (
    "banner_template",
    "banner_size",
    "url_base",
    "s3_bucket",
    "cloudfront_distribution_id",
    "date_timezone",
)


class CertificateTemplateError(Exception):
    """Domain error mapped to HTTP status codes by management_api."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code


def _resolve(slug: str) -> tuple[Course, Cohort | None]:
    course = Course.objects.filter(slug=slug).first()
    if course is not None:
        return course, None
    cohort = Cohort.objects.select_related("course").filter(slug=slug).first()
    if cohort is None:
        raise CertificateTemplateError(404, "unknown_course", "Course was not found.")
    return cohort.course, cohort


def _serialize(
    template: CertificateTemplate, course: Course, cohort: Cohort | None
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "course": course.slug,
        "course_title": course.title,
        **{field: getattr(template, field) for field in WRITABLE_FIELDS},
        "cohort": None,
        "last_project_deadline": None,
    }
    if cohort is not None:
        deadlines = list(
            Project.objects.filter(course=cohort)
            .order_by("-submission_due_date")
            .values_list("submission_due_date", flat=True)[:1]
        )
        result["cohort"] = {
            "slug": cohort.slug,
            "title": cohort.title,
            "year": cohort.year,
            # Same layout the legacy pipeline used: <family>/<year>/<hash>.pdf
            "s3_prefix": f"{course.slug}/{cohort.year}",
        }
        if deadlines:
            result["last_project_deadline"] = deadlines[0].isoformat()
    return result


def read_certificate_template(
    payload: dict[str, Any], *, course_slug: str, context=None
) -> dict[str, Any]:
    del payload, context
    course, cohort = _resolve(course_slug)
    template = CertificateTemplate.objects.filter(course=course).first()
    if template is None:
        raise CertificateTemplateError(
            404, "no_certificate_template", "This course has no certificate template."
        )
    return _serialize(template, course, cohort)


def write_certificate_template(
    payload: dict[str, Any], *, course_slug: str, context=None
) -> dict[str, Any]:
    del context
    unknown = sorted(set(payload) - set(WRITABLE_FIELDS))
    if unknown:
        raise CertificateTemplateError(400, "unknown_field", f"Unknown fields: {unknown}.")
    course, cohort = _resolve(course_slug)
    template = CertificateTemplate.objects.filter(course=course).first() or CertificateTemplate(
        course=course
    )
    for field, value in payload.items():
        if not isinstance(value, str):
            raise CertificateTemplateError(400, "invalid_field", f"{field} must be a string.")
        setattr(template, field, value.strip())
    try:
        template.full_clean()
    except ValidationError as exc:
        raise CertificateTemplateError(400, "validation_error", "; ".join(exc.messages)) from exc
    template.save()
    return _serialize(template, course, cohort)
