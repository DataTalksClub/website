"""Management capabilities for per-course certificate templates."""

from __future__ import annotations

from core.capabilities import (
    AdapterMetadata,
    Capability,
    ConcurrencyPolicy,
    IdempotencyPolicy,
    ServiceKind,
)
from courses.services.certificate_template_admin import (
    WRITABLE_FIELDS,
    read_certificate_template,
    write_certificate_template,
)

_EMPTY_RESULT = {
    "course": "",
    "course_title": "",
    "banner_template": "",
    "banner_size": "",
    "url_base": "",
    "s3_bucket": "",
    "cloudfront_distribution_id": "",
    "date_timezone": "",
    "cohort": None,
    "last_project_deadline": None,
}

CERTIFICATE_TEMPLATE_READ = Capability(
    key="courses.certificate_template.read",
    description="Read a course's certificate template and its last project deadline",
    service_kind=ServiceKind.QUERY,
    service=read_certificate_template,
    django_permission="courses.view_certificatetemplate",
    studio=AdapterMetadata(
        route="studio:course-certificate-template",
        method="GET",
        operation_id="courses.certificate_template.read.html",
    ),
    admin_api=AdapterMetadata(
        route="/api/v1/admin/courses/{slug}/certificate-template",
        method="GET",
        operation_id="admin.courses.certificate_template.read",
        scopes=("courses.certificate_template.read",),
        result_schema="CourseCertificateTemplateResponse",
        rate_class="read",
        rate_cost=1,
    ),
    idempotency=IdempotencyPolicy.NONE,
    concurrency=ConcurrencyPolicy.NONE,
    audit_action="courses.certificate_template.read",
    redacted_fields=("authorization", "cookie"),
    test_factory=lambda: dict(_EMPTY_RESULT),
)

CERTIFICATE_TEMPLATE_WRITE = Capability(
    key="courses.certificate_template.write",
    description="Create or update a course's certificate template",
    service_kind=ServiceKind.COMMAND,
    service=write_certificate_template,
    django_permission="courses.change_certificatetemplate",
    studio=AdapterMetadata(
        route="studio:course-certificate-template-write",
        method="POST",
        operation_id="courses.certificate_template.write.html",
        writable_fields=WRITABLE_FIELDS,
    ),
    admin_api=AdapterMetadata(
        route="/api/v1/admin/courses/{slug}/certificate-template",
        method="PUT",
        operation_id="admin.courses.certificate_template.write",
        scopes=("courses.certificate_template.write",),
        request_schema="CourseCertificateTemplateRequest",
        result_schema="CourseCertificateTemplateResponse",
        writable_fields=WRITABLE_FIELDS,
        rate_class="write",
        rate_cost=2,
    ),
    idempotency=IdempotencyPolicy.OPTIONAL,
    concurrency=ConcurrencyPolicy.NONE,
    audit_action="courses.certificate_template.written",
    redacted_fields=("authorization", "cookie"),
    test_factory=lambda: dict(_EMPTY_RESULT),
)

CERTIFICATE_TEMPLATE_CAPABILITIES = (CERTIFICATE_TEMPLATE_READ, CERTIFICATE_TEMPLATE_WRITE)
