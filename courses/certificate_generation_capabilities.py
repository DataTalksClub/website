"""Management capabilities that render and issue certificates for a cohort."""

from __future__ import annotations

from core.capabilities import (
    AdapterMetadata,
    Capability,
    ConcurrencyPolicy,
    IdempotencyPolicy,
    ServiceKind,
)
from courses.services.certificates import issue_service, list_service, preview_service

_PREVIEW_EMPTY = {
    "cohort": "",
    "name": "",
    "format": "pdf",
    "date": "",
    "content_type": "application/pdf",
    "filename": "",
    "content_base64": "",
}
_ISSUE_EMPTY = {
    "cohort": "",
    "date": "",
    "graduate_count": 0,
    "graduates": [],
    "executed": False,
    "issued": [],
}

CERTIFICATE_PREVIEW = Capability(
    key="courses.certificate.preview",
    description="Render one throwaway certificate for a cohort (nothing is published)",
    service_kind=ServiceKind.COMMAND,
    service=preview_service,
    django_permission="courses.view_certificatetemplate",
    studio=AdapterMetadata(
        route="studio:cohort-certificate-preview",
        method="POST",
        operation_id="courses.certificate.preview.html",
    ),
    admin_api=AdapterMetadata(
        route="/api/v1/admin/cohorts/{slug}/certificates/preview",
        method="POST",
        operation_id="admin.cohorts.certificate.preview",
        scopes=("courses.certificate.preview",),
        request_schema="CohortCertificatePreviewRequest",
        result_schema="CohortCertificatePreviewResponse",
        rate_class="write",
        rate_cost=5,
    ),
    idempotency=IdempotencyPolicy.OPTIONAL,
    concurrency=ConcurrencyPolicy.NONE,
    audit_action="courses.certificate.previewed",
    redacted_fields=("authorization", "cookie"),
    test_factory=lambda: dict(_PREVIEW_EMPTY),
)

CERTIFICATE_ISSUE = Capability(
    key="courses.certificate.issue",
    description="Plan or issue (render, publish, record URL) certificates for graduates",
    service_kind=ServiceKind.COMMAND,
    service=issue_service,
    django_permission="courses.change_certificatetemplate",
    studio=AdapterMetadata(
        route="studio:cohort-certificate-issue",
        method="POST",
        operation_id="courses.certificate.issue.html",
    ),
    admin_api=AdapterMetadata(
        route="/api/v1/admin/cohorts/{slug}/certificates/issue",
        method="POST",
        operation_id="admin.cohorts.certificate.issue",
        scopes=("courses.certificate.issue",),
        request_schema="CohortCertificateIssueRequest",
        result_schema="CohortCertificateIssueResponse",
        rate_class="write",
        rate_cost=10,
    ),
    idempotency=IdempotencyPolicy.OPTIONAL,
    concurrency=ConcurrencyPolicy.NONE,
    audit_action="courses.certificate.issued",
    redacted_fields=("authorization", "cookie"),
    test_factory=lambda: dict(_ISSUE_EMPTY),
)

CERTIFICATE_LIST = Capability(
    key="courses.certificate.list",
    description="Certificates",
    service_kind=ServiceKind.QUERY,
    service=list_service,
    django_permission="courses.view_certificatetemplate",
    studio=AdapterMetadata(
        route="studio:certificates",
        method="GET",
        operation_id="courses.certificate.list.html",
    ),
    admin_api=AdapterMetadata(
        route="/api/v1/admin/certificates",
        method="GET",
        operation_id="admin.certificates.list",
        scopes=("courses.certificate.list",),
        result_schema="CertificateCohortListResponse",
        rate_class="read",
        rate_cost=2,
    ),
    idempotency=IdempotencyPolicy.NONE,
    concurrency=ConcurrencyPolicy.NONE,
    audit_action="courses.certificate.listed",
    redacted_fields=("authorization", "cookie"),
    test_factory=lambda: {"items": []},
)

CERTIFICATE_GENERATION_CAPABILITIES = (CERTIFICATE_LIST, CERTIFICATE_PREVIEW, CERTIFICATE_ISSUE)
