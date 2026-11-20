"""Certificate generation for a cohort.

Graduates come from the site database (CMP history is already imported), the date printed is the
deadline of the cohort's last project, the rendering is done by the Banner Generator with the
course's ``CertificateTemplate.banner_template``, and issued PDFs/PNGs are published to the
course's S3 bucket.  ``preview_certificate`` renders one throwaway certificate (any name) and never
touches S3, the enrollments, or CMP.
"""

from __future__ import annotations

import base64
import hashlib
import io
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

import requests
from django.conf import settings
from django.db.models import Count, Q

from courses.models.certificate import CertificateTemplate
from courses.models.cohort import Cohort, Enrollment
from courses.models.project import Project

CERT_WIDTH = 1920
CERT_HEIGHT = 1080
PREVIEW_URL = "https://www.youtube.com/watch?v=xvFZjo5PgG0"


class CertificateError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class Graduate:
    enrollment_id: int
    name: str
    certificate_id: str
    existing_url: str | None


def get_template(cohort: Cohort) -> CertificateTemplate:
    template = CertificateTemplate.objects.filter(course=cohort.course).first()
    if template is None:
        raise CertificateError(
            "no_certificate_template", "This course has no certificate template.", 404
        )
    return template


def last_project_deadline(cohort: Cohort):
    deadline = (
        Project.objects.filter(course=cohort)
        .order_by("-submission_due_date")
        .values_list("submission_due_date", flat=True)
        .first()
    )
    if deadline is None:
        raise CertificateError(
            "no_project_deadline", "The cohort has no projects, so there is no certificate date."
        )
    return deadline


def certificate_date(cohort: Cohort, template: CertificateTemplate) -> str:
    """The last project's submission deadline as ``9 February 2026`` in the template timezone."""
    local = last_project_deadline(cohort).astimezone(ZoneInfo(template.date_timezone))
    return f"{local.day} {local:%B %Y}"


def certificate_hash(email: str) -> str:
    """Same scheme as the legacy zoomcamp-scoring pipeline, so existing URLs keep resolving."""
    return hashlib.sha1((email + "_").lower().encode("utf-8")).hexdigest()  # noqa: S324


def qr_data_uri(url: str) -> str:
    import segno

    buffer = io.BytesIO()
    segno.make(url, error="m").save(buffer, kind="svg", scale=4, border=1, dark="#000000")
    return "data:image/svg+xml;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def object_key(template: CertificateTemplate, cohort: Cohort, cert_id: str, ext: str) -> str:
    return f"{cohort.course.slug}/{cohort.year}/{cert_id}.{ext}"


def certificate_url(template: CertificateTemplate, cohort: Cohort, cert_id: str) -> str:
    return f"{template.url_base}/{object_key(template, cohort, cert_id, 'pdf')}"


def graduates(cohort: Cohort) -> list[Graduate]:
    """Enrollments with at least ``min_projects_to_pass`` passed project submissions."""
    rows = (
        Enrollment.objects.filter(course=cohort)
        .select_related("student")
        .annotate(
            passed_projects=Count(
                "projectsubmission",
                filter=Q(projectsubmission__passed=True, projectsubmission__project__course=cohort),
                distinct=True,
            )
        )
        .filter(passed_projects__gte=max(cohort.min_projects_to_pass, 1))
        .order_by("id")
    )
    result = []
    for enrollment in rows:
        name = (
            (enrollment.certificate_name or "").strip()
            or enrollment.student.get_full_name().strip()
            or enrollment.display_name
        )
        result.append(
            Graduate(
                enrollment_id=enrollment.pk,
                name=name,
                certificate_id=certificate_hash(enrollment.student.email),
                existing_url=enrollment.certificate_url,
            )
        )
    return result


def render(template: CertificateTemplate, fmt: str, spec: dict[str, Any]) -> bytes:
    if not settings.BANNER_GENERATOR_URL:
        raise CertificateError(
            "renderer_not_configured", "BANNER_GENERATOR_URL is not configured.", 503
        )
    payload: dict[str, Any] = {
        "template": template.banner_template,
        "size": template.banner_size,
        "width": CERT_WIDTH,
        "height": CERT_HEIGHT,
        "format": fmt,
        "data": spec,
    }
    headers = {}
    if settings.BANNER_GENERATOR_TOKEN:
        headers["Authorization"] = f"Bearer {settings.BANNER_GENERATOR_TOKEN}"
    try:
        response = requests.post(
            settings.BANNER_GENERATOR_URL,
            json=payload,
            headers=headers,
            timeout=settings.BANNER_GENERATOR_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise CertificateError("renderer_unreachable", f"Renderer call failed: {exc}", 502) from exc
    if response.status_code != 200 or not response.content:
        raise CertificateError(
            "renderer_error", f"Renderer returned HTTP {response.status_code}.", 502
        )
    return response.content


def _spec(template, cohort, name: str, cert_id: str) -> dict:
    url = certificate_url(template, cohort, cert_id)
    return {
        "name": name,
        "course_name": cohort.title,
        "dates": certificate_date(cohort, template),
        "certificate_id": cert_id[:6],
        "certificate_url": url,
        "qr": qr_data_uri(url),
    }


def preview_certificate(cohort: Cohort, name: str, fmt: str = "pdf") -> bytes:
    """Render one certificate for ``name``; publishes nothing and changes no rows."""
    name = name.strip()
    if not name:
        raise CertificateError("name_required", "Name is required.")
    if fmt not in ("pdf", "png"):
        raise CertificateError("bad_format", "Format must be pdf or png.")
    template = get_template(cohort)
    cert_id = "preview" + hashlib.sha1(name.encode()).hexdigest()[:34]  # noqa: S324
    spec = _spec(template, cohort, name, cert_id)
    # Like the legacy pipeline's Rick Astley row: the QR/link of a preview must not look issued.
    spec["certificate_url"] = PREVIEW_URL
    spec["qr"] = qr_data_uri(PREVIEW_URL)
    return render(template, fmt, spec)


def _s3_client(template: CertificateTemplate):
    import boto3  # type: ignore[import-untyped]

    kwargs = {"region_name": settings.CERTIFICATE_AWS_REGION}
    if settings.CERTIFICATE_AWS_ACCESS_KEY_ID:
        kwargs["aws_access_key_id"] = settings.CERTIFICATE_AWS_ACCESS_KEY_ID
        kwargs["aws_secret_access_key"] = settings.CERTIFICATE_AWS_SECRET_ACCESS_KEY
    return boto3.client("s3", **kwargs)


def issue_certificates(
    cohort: Cohort, *, enrollment_ids: list[int] | None = None, s3_client=None
) -> list[dict]:
    """Render, publish to S3, and record ``Enrollment.certificate_url`` for graduates.

    Real certificates: callers must make this an explicit staff action.
    """
    template = get_template(cohort)
    s3 = s3_client or _s3_client(template)
    issued = []
    for graduate in graduates(cohort):
        if enrollment_ids is not None and graduate.enrollment_id not in enrollment_ids:
            continue
        spec = _spec(template, cohort, graduate.name, graduate.certificate_id)
        for fmt, content_type in (("pdf", "application/pdf"), ("png", "image/png")):
            s3.put_object(
                Bucket=template.s3_bucket,
                Key=object_key(template, cohort, graduate.certificate_id, fmt),
                Body=render(template, fmt, spec),
                ContentType=content_type,
            )
        url = spec["certificate_url"]
        Enrollment.objects.filter(pk=graduate.enrollment_id).update(certificate_url=url)
        issued.append({"enrollment_id": graduate.enrollment_id, "name": graduate.name, "url": url})
    return issued


def write_urls_to_cmp(cohort: Cohort, issued: list[dict], *, session=None) -> int:
    """POST issued certificate URLs to CMP ``/api/courses/{slug}/certificates``."""
    if not settings.CMP_STAFF_API_TOKEN:
        raise CertificateError("cmp_not_configured", "CMP_STAFF_API_TOKEN is not configured.", 503)
    http = session or requests
    email_by_enrollment = dict(
        Enrollment.objects.filter(pk__in=[i["enrollment_id"] for i in issued]).values_list(
            "pk", "student__email"
        )
    )
    body = {
        "certificates": [
            {"email": email_by_enrollment[i["enrollment_id"]], "certificate_path": i["url"]}
            for i in issued
        ]
    }
    response = http.post(
        f"{settings.CMP_BASE_URL}/api/courses/{cohort.slug}/certificates",
        json=body,
        headers={"Authorization": f"Bearer {settings.CMP_STAFF_API_TOKEN}"},
        timeout=60,
    )
    if response.status_code >= 300:
        raise CertificateError("cmp_error", f"CMP returned HTTP {response.status_code}.", 502)
    return len(issued)


# -- capability services (Studio button, admin API / dtc CLI) ---------------------------


def _cohort(slug: str) -> Cohort:
    cohort = Cohort.objects.select_related("course").filter(slug=slug).first()
    if cohort is None:
        raise CertificateError("unknown_cohort", "Cohort was not found.", 404)
    return cohort


def preview_service(payload: dict, *, cohort_slug: str, context=None) -> dict:
    del context
    cohort = _cohort(cohort_slug)
    name = str(payload.get("name") or "")
    fmt = str(payload.get("format") or "pdf")
    content = preview_certificate(cohort, name, fmt)
    return {
        "cohort": cohort.slug,
        "name": name.strip(),
        "format": fmt,
        "date": certificate_date(cohort, get_template(cohort)),
        "content_type": "application/pdf" if fmt == "pdf" else "image/png",
        "filename": f"preview-{cohort.slug}.{fmt}",
        "content_base64": base64.b64encode(content).decode("ascii"),
    }


def issue_service(payload: dict, *, cohort_slug: str, context=None) -> dict:
    """Without ``confirm`` equal to the cohort slug this only reports the plan."""
    del context
    cohort = _cohort(cohort_slug)
    template = get_template(cohort)
    date = certificate_date(cohort, template)
    people = graduates(cohort)
    ids = payload.get("enrollment_ids")
    if ids is not None:
        if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
            raise CertificateError("bad_enrollment_ids", "enrollment_ids must be a list of ints.")
        people = [p for p in people if p.enrollment_id in ids]
    result = {
        "cohort": cohort.slug,
        "date": date,
        "graduate_count": len(people),
        "graduates": [
            {
                "enrollment_id": p.enrollment_id,
                "name": p.name,
                "url": certificate_url(template, cohort, p.certificate_id),
            }
            for p in people
        ],
        "executed": False,
        "issued": [],
    }
    if payload.get("confirm") != cohort.slug:
        return result
    issued = issue_certificates(
        cohort, enrollment_ids=[p.enrollment_id for p in people] if ids is not None else None
    )
    return {**result, "executed": True, "issued": issued}


def list_service(payload: dict, *, context=None) -> dict:
    """Cohorts whose course has a certificate template, with date and graduate count."""
    del payload, context
    cohorts = (
        Cohort.objects.select_related("course")
        .filter(course__certificate_template__isnull=False)
        .order_by("-year", "slug")
    )
    items = []
    for cohort in cohorts:
        template = cohort.course.certificate_template
        try:
            date = certificate_date(cohort, template)
        except CertificateError:
            date = None
        items.append(
            {
                "cohort": cohort.slug,
                "title": cohort.title,
                "banner_template": template.banner_template,
                "date": date,
                "graduate_count": len(graduates(cohort)),
            }
        )
    return {"items": items}
