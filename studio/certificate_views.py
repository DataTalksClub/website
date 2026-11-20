"""Studio adapters for course certificate templates."""

from __future__ import annotations

from community_base.kernel.services import ServiceContext
from django.http import JsonResponse

from courses.services.certificate_template_admin import CertificateTemplateError
from management_registry import CAPABILITY_REGISTRY
from studio.auth import capability_required


def _run(request, key: str, payload: dict, slug: str):
    capability = CAPABILITY_REGISTRY.require(key)
    context = ServiceContext.from_current(actor_ref=f"user:{request.user.pk}")
    try:
        result = capability.service(payload, course_slug=slug, context=context)
    except CertificateTemplateError as exc:
        return JsonResponse({"error": {"code": exc.code, "message": str(exc)}}, status=exc.status)
    return JsonResponse(result)


@capability_required("courses.certificate_template.read")
def course_certificate_template(request, slug: str):
    return _run(request, "courses.certificate_template.read", {}, slug)


@capability_required("courses.certificate_template.write")
def course_certificate_template_write(request, slug: str):
    return _run(request, "courses.certificate_template.write", request.POST.dict(), slug)
