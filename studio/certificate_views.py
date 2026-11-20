"""Studio adapters for course certificate templates."""

from __future__ import annotations

import base64

from community_base.kernel.services import ServiceContext
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render

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


@capability_required("courses.certificate.list")
def certificates_page(request):
    capability = CAPABILITY_REGISTRY.require("courses.certificate.list")
    context = ServiceContext.from_current(actor_ref=f"user:{request.user.pk}")
    result = capability.service({}, context=context)
    can_issue = request.user.has_perm("courses.change_certificatetemplate")
    return render(
        request,
        "studio/certificates.html",
        {
            "items": result["items"],
            "can_issue": can_issue,
            "message": request.session.pop("certificate_message", None),
        },
    )


def _generation(request, key: str, payload: dict, slug: str):
    from courses.services.certificates import CertificateError

    capability = CAPABILITY_REGISTRY.require(key)
    context = ServiceContext.from_current(actor_ref=f"user:{request.user.pk}")
    try:
        return capability.service(payload, cohort_slug=slug, context=context), None
    except CertificateError as exc:
        return None, str(exc)


@capability_required("courses.certificate.preview")
def cohort_certificate_preview(request, slug: str):
    result, error = _generation(
        request,
        "courses.certificate.preview",
        {"name": request.POST.get("name", ""), "format": request.POST.get("format", "pdf")},
        slug,
    )
    if error:
        request.session["certificate_message"] = f"Preview failed: {error}"
        return redirect("studio:certificates")
    response = HttpResponse(
        base64.b64decode(result["content_base64"]), content_type=result["content_type"]
    )
    response["Content-Disposition"] = f'inline; filename="{result["filename"]}"'
    return response


@capability_required("courses.certificate.issue")
def cohort_certificate_issue(request, slug: str):
    payload = {"confirm": request.POST.get("confirm", "")}
    result, error = _generation(request, "courses.certificate.issue", payload, slug)
    if error:
        request.session["certificate_message"] = f"Issuing failed: {error}"
    elif result["executed"]:
        request.session["certificate_message"] = (
            f"Issued {len(result['issued'])} certificates for {slug} (date {result['date']})."
        )
    else:
        request.session["certificate_message"] = (
            "Nothing issued: type the cohort slug in the confirmation box to issue "
            f"{result['graduate_count']} certificates."
        )
    return redirect("studio:certificates")
