"""Authenticated JSON admin API views for course certificate templates."""

from __future__ import annotations

from typing import Any

from community_base.kernel.services import ServiceContext
from django.http import JsonResponse

from courses.services.certificate_template_admin import CertificateTemplateError
from management_api.dispatch import admin_capability
from management_api.errors import APIError
from management_api.json_input import parse_json_object
from management_registry import CAPABILITY_REGISTRY


def _invoke(request: Any, key: str, payload: dict[str, Any], *, course_slug: str):
    capability = CAPABILITY_REGISTRY.require(key)
    identity = request.api_identity
    context = ServiceContext.from_current(
        actor_ref=f"api_principal:{identity.principal.pk}",
        idempotency_key=request.headers.get("Idempotency-Key") or None,
    )
    try:
        result = capability.service(payload, course_slug=course_slug, context=context)
    except CertificateTemplateError as exc:
        raise APIError(exc.status, exc.code, str(exc)) from exc
    return JsonResponse(result, status=200)


@admin_capability("courses.certificate_template.read")
def course_certificate_template_read(request: Any, slug: str) -> JsonResponse:
    return _invoke(request, "courses.certificate_template.read", {}, course_slug=slug)


@admin_capability("courses.certificate_template.write")
def course_certificate_template_write(request: Any, slug: str) -> JsonResponse:
    return _invoke(
        request,
        "courses.certificate_template.write",
        parse_json_object(request),
        course_slug=slug,
    )
