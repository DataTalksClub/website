"""Authenticated JSON admin API views for course peer-review operations."""

from __future__ import annotations

from typing import Any

from community_base.kernel.services import ServiceContext
from django.http import JsonResponse

from courses.services.peer_review_admin import PeerReviewAdminError
from management_api.dispatch import admin_capability
from management_api.errors import APIError
from management_api.json_input import parse_json_object
from management_registry import CAPABILITY_REGISTRY


def _invoke(
    request: Any,
    key: str,
    payload: dict[str, Any],
    *,
    course_slug: str,
) -> JsonResponse:
    capability = CAPABILITY_REGISTRY.require(key)
    identity = request.api_identity
    context = ServiceContext.from_current(
        actor_ref=f"api_principal:{identity.principal.pk}",
        idempotency_key=request.headers.get("Idempotency-Key") or None,
    )
    try:
        result = capability.service(payload, course_slug=course_slug, context=context)
    except PeerReviewAdminError as exc:
        raise APIError(exc.status, exc.code, str(exc)) from exc
    return JsonResponse(result, status=200)


@admin_capability("courses.peer_reviews.read")
def course_peer_reviews(request: Any, slug: str) -> JsonResponse:
    return _invoke(
        request,
        "courses.peer_reviews.read",
        request.GET.dict(),
        course_slug=slug,
    )


@admin_capability("courses.peer_reviews.assign")
def course_peer_review_assignments(request: Any, slug: str) -> JsonResponse:
    payload = parse_json_object(request) if request.body else {}
    return _invoke(
        request,
        "courses.peer_reviews.assign",
        payload,
        course_slug=slug,
    )
