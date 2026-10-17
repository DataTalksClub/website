"""HTML adapters use the same command services as authenticated clients."""

import json
import uuid

from django.contrib import messages
from django.shortcuts import redirect, render

from core.idempotency import IdempotencyConflict, IdempotencyInProgress
from core.services import ServiceContext
from courses.services.launch_validation import LaunchError
from management_registry import CAPABILITY_REGISTRY
from studio.auth import capability_required


@capability_required("courses.launch.read")
def launch_read(request):
    capability = CAPABILITY_REGISTRY.require("courses.launch.read")
    try:
        result = capability.service(request.GET.dict())
    except LaunchError as error:
        result = {"items": [], "page": 1, "total_count": 0}
        request.session["launch_error"] = str(error)
    from courses.services.launch_queries import RESOURCES

    collections = {}
    for name in ("templates", "versions", "courses", "cohorts", "assignments", "plans"):
        model, serializer = RESOURCES[name]
        rows = model._default_manager.order_by("pk")
        if name == "versions":
            from courses.services.launch_assignments import latest_publications

            rows = latest_publications()
        collections[name] = [serializer(row) for row in rows[:50]]
    return render(
        request,
        "studio/course_launch.html",
        {
            "result": result,
            "collections": collections,
            "idempotency_key": str(uuid.uuid4()),
            "submitted": request.session.pop("launch_submitted", {}),
            "launch_error": request.session.pop("launch_error", ""),
        },
    )


def form_payload(request):
    payload = {}
    for name, value in request.POST.items():
        if name in {"csrfmiddlewaretoken", "idempotency_key"}:
            continue
        if name in {"expected_revision", "offset_minutes", "year"}:
            value = int(value)
        elif name in {"confirmed", "archived", "override_defaults"}:
            value = value == "true"
        elif name == "version_ids":
            value = request.POST.getlist(name)
        elif name == "context":
            value = json.loads(value or "{}")
        elif name == "starts_at" and not value:
            value = None
        payload[name] = value
    return clean_form_payload(payload, request)


def clean_form_payload(payload, request):
    action = payload.get("action")
    for name in ("id", "course_id", "cohort_id", "scheduled_at"):
        if payload.get(name) == "":
            payload.pop(name)
    if action == "send":
        payload.pop("scheduled_at", None)
    if action in {"course_create", "assignment_save"}:
        payload.setdefault("version_ids", [])
    if action == "assignment_save":
        payload["override_defaults"] = "override_defaults" in request.POST
        if "cohort_id" in payload:
            payload.pop("course_id", None)
            payload.pop("telegram_destination_key", None)
        else:
            payload.pop("override_defaults", None)
    return payload


def invoke(request, key):
    capability = CAPABILITY_REGISTRY.require(key)
    try:
        context = ServiceContext.from_current(
            actor_ref=f"user:{request.user.pk}",
            idempotency_key=request.POST.get("idempotency_key") or "",
        )
        result = capability.service(form_payload(request), context=context)
        messages.success(
            request,
            f"Saved {request.POST.get('action', 'announcement')}. "
            f"Reference: {result.get('id', '')}",
        )
    except (LaunchError, IdempotencyConflict, IdempotencyInProgress, ValueError) as error:
        request.session["launch_submitted"] = safe_submitted(request)
        request.session["launch_error"] = str(error)
    return redirect("studio:course-launch-read")


def safe_submitted(request):
    allowed = {
        "action",
        "name",
        "subject",
        "body",
        "title",
        "slug",
        "id",
        "cohort_id",
        "course_id",
        "target_kind",
        "target_id",
        "starts_at",
        "expected_revision",
        "provenance",
    }
    result = {}
    for name, value in request.POST.items():
        if name in allowed:
            result[name] = value
    return result


@capability_required("courses.launch.write")
def launch_write(request):
    return invoke(request, "courses.launch.write")


@capability_required("courses.launch.send")
def launch_send(request):
    return invoke(request, "courses.launch.send")
