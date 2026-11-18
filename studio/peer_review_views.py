"""Studio HTML adapters for course peer-review operations."""

from __future__ import annotations

from community_base.kernel.services import ServiceContext
from django.http import JsonResponse

from courses.services.peer_review_admin import PeerReviewAdminError
from management_registry import CAPABILITY_REGISTRY
from studio.auth import capability_required


@capability_required("courses.peer_reviews.read")
def course_peer_reviews(request, slug: str):
    capability = CAPABILITY_REGISTRY.require("courses.peer_reviews.read")
    context = ServiceContext.from_current(actor_ref=f"user:{request.user.pk}")
    try:
        result = capability.service(request.GET.dict(), course_slug=slug, context=context)
    except PeerReviewAdminError as exc:
        return JsonResponse({"error": {"code": exc.code, "message": str(exc)}}, status=exc.status)
    return JsonResponse(result)


@capability_required("courses.peer_reviews.assign")
def course_peer_review_assignments(request, slug: str):
    capability = CAPABILITY_REGISTRY.require("courses.peer_reviews.assign")
    context = ServiceContext.from_current(actor_ref=f"user:{request.user.pk}")
    try:
        result = capability.service(request.POST.dict(), course_slug=slug, context=context)
    except PeerReviewAdminError as exc:
        return JsonResponse({"error": {"code": exc.code, "message": str(exc)}}, status=exc.status)
    return JsonResponse(result)
