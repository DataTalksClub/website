from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from course_management.observability import record_event
from courses.models.learner_profile import (
    ensure_learner_profile,
    learner_profile_for,
    profile_field_default,
)


LOCAL_ACCOUNT_TOGGLE_FIELDS = {
    "dark_mode",
}


def _toggle_value(user, field: str) -> bool:
    profile = learner_profile_for(user)
    value = getattr(profile, field) if profile is not None else profile_field_default(field)
    return bool(value)


@login_required
@require_POST
def toggle_dark_mode(request):
    user = request.user
    profile = ensure_learner_profile(user)
    profile.dark_mode = not profile.dark_mode
    profile.save(update_fields=["dark_mode"])
    record_event(
        "account.toggle_updated",
        request=request,
        properties={
            "field": "dark_mode",
            "enabled": profile.dark_mode,
        },
    )
    payload = {"dark_mode": profile.dark_mode}
    response = JsonResponse(payload)
    return response


@login_required
@require_POST
def update_account_toggle(request):
    field = request.POST.get("field", "")
    value = request.POST.get("value", "")

    if field not in LOCAL_ACCOUNT_TOGGLE_FIELDS:
        response = JsonResponse(
            {"error": "Unsupported account setting."},
            status=400,
        )
        return response

    enabled = value.lower() in {"1", "true", "yes", "on"}
    profile = ensure_learner_profile(request.user)
    setattr(profile, field, enabled)
    profile.save(update_fields=[field])
    record_event(
        "account.toggle_updated",
        request=request,
        properties={
            "field": field,
            "enabled": enabled,
        },
    )

    payload = {
        "field": field,
        "value": enabled,
    }
    if field == "dark_mode":
        payload["dark_mode"] = enabled
    response = JsonResponse(payload)
    return response
